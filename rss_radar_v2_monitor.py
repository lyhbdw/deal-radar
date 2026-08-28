#!/usr/bin/env python3
"""RSS Radar v2 monitor — fetch + message formatting only.
Used by feedsentinel_multi_monitor.py. Legacy standalone main() removed.
"""
import html,json,urllib.request,urllib.error
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from rss_radar_v2_core import clamp_telegram_text

def clean(s):return s.replace('\x1b','')
def text(s):return ' '.join(html.unescape(s.replace('\x1b','')).split())
def strip_html(s): return text(s)
def parse(raw):
 root=ET.fromstring(raw);out=[]
 for x in root.findall('.//item'):
  creator=x.find('{http://purl.org/dc/elements/1.1/}creator')
  out.append({'guid':(x.findtext('guid') or x.findtext('link') or '').strip(),'title':(x.findtext('title') or '').strip(),'link':(x.findtext('link') or '').strip(),'description':text(x.findtext('description') or ''),'pub_date':(x.findtext('pubDate') or '').strip(),'category':(x.findtext('category') or '').strip(),'author':creator.text.strip() if creator is not None and creator.text else ''})
 return [p for p in out if p['guid']]
def fetch(source):
 """Fetch RSS via urllib — no subprocess fork overhead."""
 req=urllib.request.Request(source['rss_url'],headers={'User-Agent':'Mozilla/5.0'})
 with urllib.request.urlopen(req,timeout=10) as r:body=r.read()
 posts=parse(body.decode(errors='replace'))
 if not posts:raise RuntimeError('zero parsed items')
 return posts
def message(p,matched,src):
 try:when=parsedate_to_datetime(p['pub_date']).strftime('%m-%d %H:%M')
 except Exception:when=p['pub_date'][:16]
 e=lambda x:html.escape(str(x),quote=True)
 lines=[f"{src['emoji']} <b>{e(src['name'])} 命中</b>",'━━━━━━━━━━━━',f"<b>{e(p['title'][:600])}</b>"]
 if p['description']:lines.extend(['',e(p['description'][:700])])
 meta=' · '.join(x for x in (p['category'],p['author'],when) if x)
 if meta:lines.extend(['',f'<code>{e(meta)}</code>'])
 lines.extend([f"🏷 {' '.join('#'+x.replace(' ','_') for x in matched)}",f'🔗 <a href="{e(p["link"])}">打开原帖</a>'])
 return clamp_telegram_text('\n'.join(lines))
