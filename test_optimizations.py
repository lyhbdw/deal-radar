"""离线验证本轮优化的三个修复点，不打真实网络/Telegram。"""
import re
import sys
import unittest

sys.path.insert(0, "/root")

from nodeseek_monitor import strip_html, clean_text
from nodeseek_core import get_source, SOURCES


class StripHtmlTests(unittest.TestCase):
    def test_discourse_html_description_becomes_plain_text(self):
        raw = (
            '<h1><a name="p-1" class="anchor" href="https://idcflare.com#p-1"></a>'
            '<img src="https://idcflare.com/images/emoji/twemoji/fire.png" title=":fire:" class="emoji">'
            '【优惠车发车】美日德线路</h1>'
            '<p>多区域长期小车，覆盖 <strong>美国、日本、德国</strong>。</p>'
            '<p>CN2 / 9929 / CMIN2 等线路<br>常用流媒体解锁</p>'
        )
        out = strip_html(clean_text(raw))
        self.assertNotIn("<", out)
        self.assertNotIn("img", out)
        self.assertIn("优惠车发车", out)
        self.assertIn("美日德线路", out)
        self.assertIn("CN2 / 9929 / CMIN2", out)
        self.assertIn("流媒体解锁", out)

    def test_html_entities_unescaped(self):
        out = strip_html(clean_text("<p>A &amp; B &lt;test&gt;</p>"))
        self.assertEqual(out, "A & B <test>")

    def test_whitespace_collapsed(self):
        out = strip_html(clean_text("<p>hello</p>   <p>world</p>"))
        self.assertEqual(out, "hello world")

    def test_plain_text_passthrough(self):
        # sb.sb / NodeSeek 的纯文本描述不应被破坏
        out = strip_html(clean_text("出个云悠德国 9929 不限流量"))
        self.assertEqual(out, "出个云悠德国 9929 不限流量")


class CategoryRegistryTests(unittest.TestCase):
    def test_nodeseek_promotion_category_registered(self):
        nodeseek = get_source("nodeseek")
        self.assertIn("promotion", nodeseek["categories"])

    def test_all_sources_have_their_seen_categories_registered(self):
        """源注册表必须覆盖各源 RSS 实际出现过的分类。"""
        seen = {
            "nodeseek": ["daily", "review", "trade", "carpool", "tech", "info", "expose", "promotion", "photo-share", "dev"],
            "sbsb": ["综合", "交易", "AI", "主机", "分享", "优惠", "域名", "公告", "推广", "硬件"],
            "idcflare": ["交易", "求助", "测评", "茶馆", "运营", "福利"],
        }
        for s in SOURCES:
            for cat in seen[s["id"]]:
                self.assertIn(cat, s["categories"], f"{s['id']} 缺少分类 {cat}")


if __name__ == "__main__":
    unittest.main()
