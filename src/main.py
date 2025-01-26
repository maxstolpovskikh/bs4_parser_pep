import logging
import re
from collections import Counter
from urllib.parse import urljoin

import requests_cache
from bs4 import BeautifulSoup
from tqdm import tqdm

from configs import configure_argument_parser, configure_logging
from constants import BASE_DIR, EXPECTED_STATUS, MAIN_DOC_URL, PEP_URL
from exceptions import ParserFindTagException
from outputs import control_output
from utils import find_tag, get_response


def whats_new(session):
    whats_new_url = urljoin(MAIN_DOC_URL, 'whatsnew/')
    response = get_response(session, whats_new_url)
    if response is None:
        return
    soup = BeautifulSoup(response.text, features='lxml')
    main_div = find_tag(soup, 'section', attrs={'id': 'what-s-new-in-python'})
    div_with_ul = find_tag(main_div, 'div', attrs={'class': 'toctree-wrapper'})
    sections_by_python = div_with_ul.find_all(
        'li', attrs={'class': 'toctree-l1'}
    )

    for section in tqdm(sections_by_python):
        version_a_tag = find_tag(section, 'a')

        href = version_a_tag['href']
        version_link = urljoin(whats_new_url, href)

        response = requests_cache.CachedSession()
        response = get_response(session, version_link)
        if response is None:
            continue
        soup = BeautifulSoup(response.text, features='lxml')
        h1 = find_tag(soup, 'h1')
        dl = find_tag(soup, 'dl')
        dl_text = dl.text.replace('\n', ' ')

        results = [('Ссылка на статью', 'Заголовок', 'Редактор, автор')]
        results.append(
            (version_link, h1.text, dl_text)
        )

    return results


def latest_versions(session):
    response = get_response(session, MAIN_DOC_URL)
    if response is None:
        return
    soup = BeautifulSoup(response.text, features='lxml')

    side_bar = find_tag(soup, 'div', attrs={'class': 'sphinxsidebarwrapper'})
    ul_tags = side_bar.find_all('ul')

    for ul in ul_tags:
        if 'All versions' in ul.text:
            a_tags = ul.find_all('a')
            break
    else:
        raise Exception('Ничего не нашлось')

    pattern = r'Python (?P<version>\d\.\d+) \((?P<status>.*)\)'

    results = [('Ссылка на документацию', 'Версия', 'Статус')]

    for a_tag in a_tags:
        version = text = a_tag.text
        if re.match(pattern, text):
            text_match = re.search(pattern, text)
            version, status = text_match.groups()
        results.append((a_tag['href'], version, status))

    return results


def download(session):
    downloads_url = urljoin(MAIN_DOC_URL, 'download.html')
    response = get_response(session, downloads_url)
    if response is None:
        return
    soup = BeautifulSoup(response.text, features='lxml')
    table_tag = find_tag(soup, 'table', attrs={'class': 'docutils'})
    pdf_a4_tag = find_tag(
        table_tag,
        'a',
        {'href': re.compile(r'.+pdf-a4\.zip$')}
    )
    pdf_a4_link = pdf_a4_tag['href']
    archive_url = urljoin(downloads_url, pdf_a4_link)
    filename = archive_url.split('/')[-1]
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    archive_path = downloads_dir / filename
    response = get_response(session, archive_url)
    if response is None:
        return
    with open(archive_path, 'wb') as file:
        file.write(response.content)

    logging.info(f'Архив был загружен и сохранён: {archive_path}')


def get_pep_rows(soup):
    rows = []
    for tbody in soup.find_all('tbody'):
        rows.extend(tbody.find_all('tr'))
    return rows


def process_row_status(cells):
    abbr_tag = find_tag(cells[0], 'abbr')
    text = abbr_tag.text
    a_tag = find_tag(cells[1], 'a')
    table_status = abbr_tag['title']
    final_status = table_status.split(',')[-1].strip()
    return text, a_tag, final_status


def check_page_status(page_soup, text, final_status, status_counter):
    for dt in page_soup.find_all('dt'):
        if 'Status' in dt.text:
            status_dd = dt.find_next('dd')
            if not status_dd:
                continue
            status_abbr = find_tag(status_dd, 'abbr')
            if not status_abbr:
                continue
            page_st = status_abbr.text
            status_counter[page_st] += 1
            p_status = text[1] if len(text) > 1 else ''
            if page_st not in EXPECTED_STATUS[p_status]:
                return (f'Несовпадающие статусы:\n'
                        f'PEP: {text}\n'
                        f'Статус в таблице: {final_status}\n'
                        f'Статус в карточке: {page_st}')


def pep(session):
    errors = []
    response = get_response(session, PEP_URL)
    if not response:
        return

    soup = BeautifulSoup(response.text, features='lxml')
    rows = get_pep_rows(soup)
    status_counter = Counter()

    for row in tqdm(rows):
        try:
            cells = row.find_all('td')
            text, a_tag, final_status = process_row_status(cells)
            pep_link = urljoin(PEP_URL, a_tag['href'])
            response = get_response(session, pep_link)
            if not response:
                continue
            page_soup = BeautifulSoup(response.text, features='lxml')
            error = check_page_status(
                page_soup,
                text,
                final_status,
                status_counter
            )
            if error:
                errors.append(error)
        except ParserFindTagException:
            continue

    if errors:
        logging.warning('\n'.join(errors))

    results = [('Статус', 'Количество')]
    results.extend((status, count) for status, count in status_counter.items())
    results.append(('Total', sum(status_counter.values())))

    return results


MODE_TO_FUNCTION = {
    'whats-new': whats_new,
    'latest-versions': latest_versions,
    'download': download,
    'pep': pep,
}


def main():
    configure_logging()

    logging.info('Парсер запущен!')

    arg_parser = configure_argument_parser(MODE_TO_FUNCTION.keys())
    args = arg_parser.parse_args()

    logging.info(f'Аргументы командной строки: {args}')

    session = requests_cache.CachedSession()
    if args.clear_cache:
        session.cache.clear()

    parser_mode = args.mode
    results = MODE_TO_FUNCTION[parser_mode](session)
    if results is not None:
        control_output(results, args)

    logging.info('Парсер завершил работу.')


if __name__ == '__main__':
    main()
