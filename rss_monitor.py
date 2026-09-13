#!/usr/bin/env python3
"""RSS Radar v2 monitor — fetch + message formatting only.
Used by feedsentinel_multi_monitor.py. Legacy standalone main() removed.
"""
import html,subprocess,urllib.request,urllib.error
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from rss_core import clamp_telegram_text

def clean(s):return s.replace('\x1b','')
def text(s):return ' '.join(html.unescape(s.replace('\x1b','')).split())
class _TextExtractor(HTMLParser):
 def __init__(self):
  super().__init__(convert_charrefs=True); self.parts=[]
 def handle_data(self,data): self.parts.append(data)

def strip_html(s):
 parser=_TextExtractor()
 try: parser.feed(s); parser.close(); return text(' '.join(parser.parts))
 except Exception: return text(s)
def parse(raw):
 root=ET.fromstring(raw);out=[]
 local=lambda tag: tag.rsplit('}',1)[-1]
 def child(node,names):
  return next((item for item in node if local(item.tag) in names),None)
 def value(node,names):
  item=child(node,names);return '' if item is None or item.text is None else item.text
 for x in root.iter():
  if local(x.tag) not in ('item','entry'):continue
  link_node=child(x,{'link'});link=((link_node.get('href') or link_node.text or '') if link_node is not None else value(x,{'link'})).strip()
  author_node=child(x,{'author'});author=value(x,{'creator','author'}).strip()
  if author_node is not None:author=value(author_node,{'name'}) or author
  guid=(value(x,{'guid','id'}) or link).strip()
  out.append({'guid':guid,'title':value(x,{'title'}).strip(),'link':link,
   'description':strip_html(value(x,{'description','summary','content'})),
   'pub_date':value(x,{'pubDate','published','updated'}).strip(),
   'category':value(x,{'category'}).strip(),'author':author})
 return [p for p in out if p['guid']]
def fetch(source):
 """Fetch RSS. Prefers urllib; falls back to curl --http2 because
 idcflare.com's Cloudflare 403s any HTTP/1.1 client fingerprint."""
 url=source['rss_url']
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
  with urllib.request.urlopen(req,timeout=10) as r:body=r.read()
 except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError):
  result=subprocess.run(['curl','-fsS','--max-time','15','--compressed','-A','FeedSentinel/1.0',url],capture_output=True,timeout=20)
  if result.returncode != 0 or not result.stdout:
   detail=result.stderr.decode(errors='replace').strip()[:300]
   raise RuntimeError(f'curl fallback failed ({result.returncode}): {detail}')
  body=result.stdout
 posts=parse(body.decode(errors='replace'))
 if not posts:raise RuntimeError('zero parsed items')
 return posts
def message(p, matched, src):
    try:
        when = parsedate_to_datetime(p['pub_date']).strftime('%m-%d %H:%M')
    except Exception:
        when = p['pub_date'][:16]
    e = lambda x: html.escape(str(x), quote=True)
    tags = ' '.join(f"#{e(x.replace(' ', '_'))}" for x in matched)
    lines = [
        f"{src['emoji']} <b>{e(src['name'])}</b>",
        f"<b>{e(p['title'][:400])}</b>"
    ]
    if p.get('description'):
        desc = p['description'].strip()
        if desc:
            lines.extend(['', f"<i>{e(desc[:260])}</i>"])
    meta = []
    if tags:
        meta.append(f"🏷 {tags}")
    if p.get('author'):
        meta.append(e(p['author']))
    if when:
        meta.append(e(when))
    lines.extend(['', ' · '.join(meta)])
    return clamp_telegram_text('\n'.join(lines))
