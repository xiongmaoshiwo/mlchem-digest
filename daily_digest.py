import os
import re
import ssl
import smtplib
import logging
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yaml

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
JST = timezone(timedelta(hours=9))

# ===== load config =====
with open('config.yaml', 'r', encoding='utf-8') as f:
    CFG = yaml.safe_load(f)

ML_KEYWORDS = [k.lower() for k in CFG.get('ml_keywords', [])]
CHEM_KEYWORDS = [k.lower() for k in CFG.get('chem_keywords', [])]
MAX_RESULTS = int(CFG.get('max_results', 60))
LOOKBACK_HOURS = int(CFG.get('lookback_hours', 30))
MIN_ITEMS_TO_EMAIL = int(CFG.get('min_items_to_email', 1))

# ===== OpenAI =====
from openai import OpenAI
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise RuntimeError('OPENAI_API_KEY is required')
client = OpenAI(api_key=OPENAI_API_KEY)

def within_lookback(dt: datetime) -> bool:
    now_utc = datetime.now(timezone.utc)
    return (now_utc - dt) <= timedelta(hours=LOOKBACK_HOURS)

def normalize_text(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", s or "").strip()

def has_keywords(text: str) -> bool:
    t = (text or "").lower()
    ml_ok = any(k in t for k in ML_KEYWORDS)
    chem_ok = any(k in t for k in CHEM_KEYWORDS)
    return ml_ok and chem_ok

def dedup(items):
    seen = set()
    out = []
    for it in items:
        key = (it.get('doi') or '').lower() or it.get('url','').lower() or it.get('title','').lower()
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out

# ===== arXiv =====
def fetch_arxiv():
    q = 'all:("machine learning" OR "LLM" OR "graph neural network" OR "materials informatics")'
    url = (
        'http://export.arxiv.org/api/query?'
        f'search_query={requests.utils.quote(q)}&'
        'sortBy=submittedDate&sortOrder=descending&'
        f'max_results={MAX_RESULTS}'
    )
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries:
        title = normalize_text(e.get('title', ''))
        summary = normalize_text(e.get('summary', ''))
        link = e.get('link')
        dt = dtparser.parse(e.get('published')) if e.get('published') else None
        if not dt:
            dt = dtparser.parse(e.get('updated')) if e.get('updated') else datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if not within_lookback(dt):
            continue
        if not has_keywords(title + ' ' + summary):
            continue
        items.append({
            'source': 'arXiv',
            'title': title,
            'abstract': summary,
            'url': link,
            'doi': None,
            'published_at': dt.astimezone(JST).isoformat(),
        })
    logging.info(f"arXiv: {len(items)} items")
    return items

# ===== Crossref =====
def fetch_crossref():
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).date().isoformat()
    q = 'machine learning LLM "graph neural network" "materials informatics" chemistry polymer corrosion MOF "organic chemistry" "interfacial chemistry" "polymer science" coating electrodeposition'
    params = {
        'query': q,
        'filter': f'from-pub-date:{since}',
        'rows': MAX_RESULTS,
        'sort': 'published',
        'order': 'desc',
    }
    url = 'https://api.crossref.org/works'
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
    except Exception as ex:
        logging.warning(f"Crossref fetch failed: {ex}")
        return []
    data = r.json()
    items = []
    for rec in data.get('message', {}).get('items', []):
        title = normalize_text(' '.join(rec.get('title', [])))
        ab = normalize_text(rec.get('abstract', ''))
        url_ = rec.get('URL')
        doi = rec.get('DOI')
        dt = None
        if 'published-print' in rec:
            parts = rec['published-print'].get('date-parts', [[None, None, None]])[0]
            y, m, d = (parts + [1,1,1])[:3]
            if y:
                dt = datetime(y, m or 1, d or 1, tzinfo=timezone.utc)
        if not dt and 'published-online' in rec:
            parts = rec['published-online'].get('date-parts', [[None, None, None]])[0]
            y, m, d = (parts + [1,1,1])[:3]
            if y:
                dt = datetime(y, m or 1, d or 1, tzinfo=timezone.utc)
        if not dt:
            created = rec.get('created', {}).get('date-time')
            if created:
                dt = dtparser.parse(created)
            else:
                dt = datetime.now(timezone.utc)
        if not within_lookback(dt):
            continue
        text = title + ' ' + ab
        if not has_keywords(text):
            continue
        items.append({
            'source': 'Crossref',
            'title': title,
            'abstract': ab,
            'url': url_,
            'doi': doi,
            'published_at': dt.astimezone(JST).isoformat(),
        })
    logging.info(f"Crossref: {len(items)} items")
    return items

# ===== bioRxiv =====
def fetch_biorxiv():
    url = 'https://connect.biorxiv.org/relate/feed/181'
    try:
        feed = feedparser.parse(url)
    except Exception as ex:
        logging.warning(f"bioRxiv fetch failed: {ex}")
        return []
    items = []
    for e in feed.entries[:MAX_RESULTS]:
        title = normalize_text(e.get('title', ''))
        summary = normalize_text(e.get('summary', ''))
        link = e.get('link')
        dt = dtparser.parse(e.get('published')) if e.get('published') else datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if not within_lookback(dt):
            continue
        if not has_keywords(title + ' ' + summary):
            continue
        items.append({
            'source': 'bioRxiv',
            'title': title,
            'abstract': summary,
            'url': link,
            'doi': None,
            'published_at': dt.astimezone(JST).isoformat(),
        })
    logging.info(f"bioRxiv: {len(items)} items")
    return items

# ===== Semantic Scholar (optional) =====
def fetch_semanticscholar():
    api_key = os.environ.get('S2_API_KEY')
    if not api_key:
        logging.info('Semantic Scholar skipped (S2_API_KEY not set)')
        return []
    headers = { 'x-api-key': api_key }
    url = 'https://api.semanticscholar.org/graph/v1/paper/search'
    query = '\"machine learning\" OR LLM OR \"graph neural network\" OR \"materials informatics\" chemistry polymer corrosion MOF \"organic chemistry\" \"interfacial chemistry\" \"polymer science\" coating electrodeposition'
    params = {
        'query': query,
        'limit': str(MAX_RESULTS),
        'fields': 'title,abstract,url,doi,publicationDate',
        'sort': 'publicationDate:desc'
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
    except Exception as ex:
        logging.warning(f"Semantic Scholar fetch failed: {ex}")
        return []
    data = r.json()
    items = []
    for p in data.get('data', []):
        title = normalize_text(p.get('title', ''))
        ab = normalize_text(p.get('abstract', ''))
        link = p.get('url')
        doi = p.get('doi')
        if p.get('publicationDate'):
            try:
                dt = dtparser.parse(p['publicationDate'])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except:
                dt = datetime.now(timezone.utc)
        else:
            dt = datetime.now(timezone.utc)
        if not within_lookback(dt):
            continue
        if not has_keywords(title + ' ' + ab):
            continue
        items.append({
            'source': 'SemanticScholar',
            'title': title,
            'abstract': ab,
            'url': link,
            'doi': doi,
            'published_at': dt.astimezone(JST).isoformat(),
        })
    logging.info(f"Semantic Scholar: {len(items)} items")
    return items

def summarize_ja(title: str, abstract: str) -> str:
    sys = (
        "あなたは学術論文要約の専門家です。"
        "入力のタイトルと要旨から、日本語で3〜4文の要約を作成してください。"
        "構成は『目的→手法→データ/対象→主結果→示唆』の順に、可能な範囲で端的にまとめてください。"
        "専門用語は簡潔に、過度な推測は避けてください。"
    )
    user = f"タイトル: {title}\n要旨: {abstract}"
    try:
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user}
            ],
            temperature=0.2,
        )
        return normalize_text(resp.choices[0].message.content)
    except Exception as ex:
        logging.warning(f"Summarize failed: {ex}")
        return (abstract or '')[:200]

def build_email_html(items):
    date_str = datetime.now(JST).strftime('%Y-%m-%d')
    html = [f"<h2>ML×Chem Daily Digest – {date_str}</h2>"]
    html.append('<ol>')
    for it in items:
        title = it['title']
        url = it['url']
        src = it['source']
        pub = it.get('published_at', '')
        summ = it.get('summary_ja', '')
        html.append('<li>')
        html.append(f"<b>タイトル</b>: {title}<br>")
        html.append(f"<b>出典</b>: {src} / <span style='font-size:90%'>{pub}</span><br>")
        if summ:
            html.append(f"<b>要約</b>: {summ}<br>")
        if it.get('doi'):
            html.append(f"<b>DOI</b>: {it['doi']}<br>")
        if url:
            html.append(f"<a href='{url}'>リンク</a>")
        html.append('</li>')
    html.append('</ol>')
    html.append('<p style=\"font-size:90%;color:#666;\">キーワード: ' +
                ', '.join(ML_KEYWORDS) + ' × ' + ', '.join(CHEM_KEYWORDS) + '</p>')
    return '\n'.join(html)

def send_email(html_body):
    host = os.environ['SMTP_HOST']
    port = int(os.environ.get('SMTP_PORT', '465'))
    user = os.environ['SMTP_USER']
    password = os.environ['SMTP_PASS']
    recipient = os.environ['RECIPIENT_EMAIL']

    msg = MIMEMultipart('alternative')
    msg['Subject'] = '[ML×Chem] Daily Digest'
    msg['From'] = user
    msg['To'] = recipient
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context) as server:
        server.login(user, password)
        server.sendmail(user, [recipient], msg.as_string())

def main():
    items = []
    items += fetch_arxiv()
    items += fetch_crossref()
    items += fetch_biorxiv()
    items += fetch_semanticscholar()  # optional

    if not items:
        logging.info('No items fetched')
        return

    items = dedup(items)

    for it in items:
        it['summary_ja'] = summarize_ja(it['title'], it.get('abstract', ''))

    if len(items) < MIN_ITEMS_TO_EMAIL:
        logging.info(f"Less than min_items_to_email ({MIN_ITEMS_TO_EMAIL}). Skip sending.")
        return

    html = build_email_html(items)
    send_email(html)
    logging.info(f"Sent {len(items)} items")

if __name__ == '__main__':
    main()
