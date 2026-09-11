#!/usr/bin/env python3
"""RSS Radar v2 monitor — fetch + message formatting only.
Used by feedsentinel_multi_monitor.py. Legacy standalone main() removed.
"""
import html,subprocess,urllib.request,urllib.error
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from rss_core import clamp_telegram_text

def clean(s):return s.replace('\x1b','')
def text(s):return ' '.join(html.unescape(s.replace('\x1b','')).split())
def strip_html(s): return text(s)
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
   'description':text(value(x,{'description','summary','content'})),
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
def message(p,matched,src):
 try:when=parsedate_to_datetime(p['pub_date']).strftime('%m-%d %H:%M')
 except Exception:when=p['pub_date'][:16]
 e=lambda x:html.escape(str(x),quote=True)
 lines=[f"{src['emoji']} <b>{e(src['name'])} 命中</b>",'━━━━━━━━━━━━',f"<b>{e(p['title'][:600])}</b>"]
 if p['description']:lines.extend(['',e(p['description'][:700])])
 meta=' · '.join(x for x in (p['category'],p['author'],when) if x)
 if meta:lines.extend(['',f'<code>{e(meta)}</code>'])
 lines.extend([f"🏷 {' '.join('#'+e(x.replace(' ','_')) for x in matched)}",f'🔗 <a href="{e(p["link"])}">打开原帖</a>'])
 return clamp_telegram_text('\n'.join(lines))
