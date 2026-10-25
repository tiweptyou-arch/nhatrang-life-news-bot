import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-for-local-unit-tests")
import bot  # noqa: E402


class NewsQualityTests(unittest.TestCase):
    def test_practical_local_story_is_kept(self):
        self.assertTrue(bot.is_relevant_news(
            "Khánh Hòa cảnh báo mưa lớn tại Nha Trang",
            "Chính quyền thông báo nguy cơ ngập ở một số tuyến đường."
        ))

    def test_irrelevant_local_sports_story_is_rejected(self):
        self.assertFalse(bot.is_relevant_news(
            "Khánh Hòa gặp Cần Thơ trong trận đấu bóng đá",
            "Bình luận trận đấu và đội hình trước giải."
        ))

    def test_generic_local_mention_without_practical_value_is_rejected(self):
        self.assertFalse(bot.is_relevant_news(
            "Một nghệ sĩ nổi tiếng đến Nha Trang",
            "Thông tin giải trí chung, không có hướng dẫn hoặc hệ quả thiết thực."
        ))

    def test_repeated_headline_lead_is_removed(self):
        body = "Mưa lớn tại Nha Trang. Chính quyền khuyến cáo người dân tránh các tuyến đường ngập."
        self.assertEqual(
            bot.remove_repeated_lead("Mưa lớn tại Nha Trang", body),
            "Chính quyền khuyến cáo người dân tránh các tuyến đường ngập."
        )

    def test_thin_duplicate_body_becomes_empty(self):
        self.assertEqual(
            bot.remove_repeated_lead("Cảnh báo mưa lớn", "Cảnh báo mưa lớn"),
            ""
        )

    def test_split_long_text_keeps_all_words(self):
        original = " ".join(["thongtin"] * 12000)
        pieces = bot.split_html_text(original, limit=3900)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece) <= 3900 for piece in pieces))
        self.assertEqual(" ".join(pieces).split(), original.split())

    def test_long_photo_post_keeps_image_and_full_body(self):
        body = " ".join(["thongtin"] * 1300) + " UNIQUE_ENDING_MARKER"
        post = "<b>Tiêu đề hữu ích</b>\n\n" + body + '\n\n<a href="https://example.com">Nguồn</a>'
        photo_response = {"message_id": 100, "chat": {"id": -1001}}
        with patch.object(bot, "api", return_value=photo_response) as api_mock, \
             patch.object(bot, "post_text", return_value={"message_id": 101}) as text_mock, \
             patch.object(bot.time, "sleep"):
            bot.post_with_image(post, "https://example.com/image.jpg", illustration=True)
        self.assertEqual(api_mock.call_args_list[0].args[0], "sendPhoto")
        caption = api_mock.call_args_list[0].args[1]["caption"]
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("Tiêu đề hữu ích", caption)
        posted_text = "\n".join(call.args[0] for call in text_mock.call_args_list)
        self.assertIn("UNIQUE_ENDING_MARKER", posted_text)
        self.assertIn("https://example.com", posted_text)
        self.assertGreaterEqual(text_mock.call_count, 1)

    def test_translation_does_not_drop_text_after_4000_characters(self):
        text = ". ".join(["Уникальный факт о городской жизни"] * 220) + ". ПОСЛЕДНЯЯ_УНИКАЛЬНАЯ_СТРОКА"
        requested = []

        def fake_get_url(url):
            query = parse_qs(urlparse(url).query).get("q", [""])[0]
            requested.append(query)
            return json.dumps([[["Переведено", None, None]]]).encode("utf-8")

        with patch.object(bot, "get_url", side_effect=fake_get_url):
            result = bot.translate(text)
        self.assertGreater(len(requested), 1)
        self.assertIn("ПОСЛЕДНЯЯ_УНИКАЛЬНАЯ_СТРОКА", " ".join(requested))
        self.assertTrue(result)

    def test_only_approved_reaction_emojis_are_suggested(self):
        examples = [
            "giao thông mở cửa đường mới ở Nha Trang",
            "tình nguyện giúp đỡ người dân tại Khánh Hòa",
            "hài hước tình huống hài ở Nha Trang",
        ]
        for example in examples:
            line = bot.reaction_line(example)
            self.assertTrue(line, example)
            self.assertTrue(line.startswith(bot.ALLOWED_REACTIONS), line)
        self.assertEqual(bot.reaction_line("bão lớn khiến nhiều người phải sơ tán"), "")

    def test_corrupt_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bot_state.json")
            original = "{broken-json"
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(original)
            with patch.object(bot, "STATE_FILE", path):
                with self.assertRaises(RuntimeError):
                    bot.load_state()
            with open(path, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), original)

    def test_atomic_state_save_preserves_existing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bot_state.json")
            old = {
                "seen_links": ["https://example.com/old"],
                "ads": [{"id": "existing-ad", "message_ids": [10, 11]}],
                "legacy_custom_field": {"keep": True},
            }
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(old, stream)
            with patch.object(bot, "STATE_FILE", path):
                state = bot.load_state()
                bot.save_state(state)
            with open(path, encoding="utf-8") as stream:
                saved = json.load(stream)
            self.assertEqual(saved["seen_links"], old["seen_links"])
            self.assertEqual(saved["ads"], old["ads"])
            self.assertEqual(saved["legacy_custom_field"], old["legacy_custom_field"])
            self.assertIn("published_news", saved)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_ad_album_sends_and_tracks_every_message_id(self):
        response = [
            {"message_id": 201, "chat": {"id": -1001}},
            {"message_id": 202, "chat": {"id": -1001}},
            {"message_id": 203, "chat": {"id": -1001}},
        ]
        ad = {
            "type": "album",
            "text": "Test album",
            "media_items": [
                {"type": "photo", "file_id": "file-a", "caption": ""},
                {"type": "photo", "file_id": "file-b", "caption": ""},
                {"type": "video", "file_id": "file-c", "caption": ""},
            ],
        }
        with patch.object(bot, "api", return_value=response) as api_mock:
            result = bot.send_ad_content(ad)
        self.assertEqual(api_mock.call_args.args[0], "sendMediaGroup")
        self.assertEqual(result["_message_ids"], [201, 202, 203])
        self.assertEqual(result["message_id"], 201)

    def test_expiring_ad_deletes_all_album_messages(self):
        ad = {"chat_id": -1001, "message_id": 201, "message_ids": [201, 202, 203]}
        with patch.object(bot, "api", return_value={}) as api_mock:
            bot.delete_ad_message(ad)
        deleted = [
            c.args[0] for c in api_mock.call_args_list
            if c.args[0] == "deleteMessage"
        ]
        self.assertEqual(len(deleted), 3)


if __name__ == "__main__":
    unittest.main()
