import logging
import re
from collections import Counter
from urllib.parse import urljoin

import requests_cache
from bs4 import BeautifulSoup
from tqdm import tqdm

from configs import configure_argument_parser, configure_logging
from constants import BASE_DIR, MAIN_DOC_URL, PEP_URL
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
        version_a_tag = section.find('a')

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
    pdf_a4_tag = table_tag.find('a', {'href': re.compile(r'.+pdf-a4\.zip$')})
    pdf_a4_link = pdf_a4_tag['href']
    archive_url = urljoin(downloads_url, pdf_a4_link)
    filename = archive_url.split('/')[-1]
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    archive_path = downloads_dir / filename
    response = session.get(archive_url)
    with open(archive_path, 'wb') as file:
        file.write(response.content)

    logging.info(f'Архив был загружен и сохранён: {archive_path}')


def pep(session):
    response = get_response(session, PEP_URL)
    if response is None:
        return

    soup = BeautifulSoup(response.text, features='lxml')
    tbody_tags = soup.find_all('tbody')
    status_counter = Counter()

    for tbody in tbody_tags:
        for row in tqdm(tbody.find_all('tr')):
            cells = row.find_all('td')
            abbr_tag = cells[0].find('abbr')
            if abbr_tag:
                a_tag = cells[1].find('a')

                if not a_tag:
                    continue

                table_status = abbr_tag['title']
                final_status = table_status.split(',')[-1].strip()
                pep_link = urljoin(PEP_URL, a_tag['href'])

                response = get_response(session, pep_link)
                if response is None:
                    continue

                page_soup = BeautifulSoup(response.text, features='lxml')
                for dt in page_soup.find_all('dt'):
                    if 'Status' in dt.text:
                        status_dd = dt.find_next('dd')
                        if status_dd:
                            status_abbr = status_dd.find('abbr')
                            if status_abbr:
                                page_status = status_abbr.text
                                status_counter[page_status] += 1

                                if final_status != page_status:
                                    error = (
                                        f'Несовпадающие статусы:\n'
                                        f'PEP: {abbr_tag.text}\n'
                                        f'Статус в таблице: {final_status}\n'
                                        f'Статус в карточке: {page_status}'
                                    )
                                    logging.warning(error)

    results = [('Статус', 'Количество')]
    for status, count in status_counter.items():
        results.append((status, count))
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
