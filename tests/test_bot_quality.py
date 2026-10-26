import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import call, patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-for-local-unit-tests")
import bot  # noqa: E402


class NewsQualityTests(unittest.TestCase):
    def test_polling_guard_accepts_missing_webhook(self):
        with patch.object(bot, "api", return_value={"url": ""}) as api_mock:
            bot.ensure_polling_no_webhook()
        self.assertEqual(api_mock.call_args.args[0], "getWebhookInfo")

    def test_polling_guard_skips_polling_when_webhook_is_active(self):
        with patch.object(bot, "api", return_value={"url": "https://example.com/webhook"}) as api_mock:
            self.assertFalse(bot.ensure_polling_no_webhook())
        self.assertEqual([call.args[0] for call in api_mock.call_args_list], ["getWebhookInfo"])

    def test_polling_guard_skips_polling_if_webhook_status_cannot_be_checked(self):
        with patch.object(bot, "api", side_effect=RuntimeError("network error")) as api_mock:
            self.assertFalse(bot.ensure_polling_no_webhook())
        self.assertEqual([call.args[0] for call in api_mock.call_args_list], ["getWebhookInfo"])

    def test_parse_rss_publication_date(self):
        parsed = bot.parse_source_date("Sun, 25 Oct 2026 10:00:00 GMT")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.hour, 17)
        self.assertEqual(parsed.tzinfo, bot.TZ)

    def test_iso_source_publication_date_is_supported(self):
        parsed = bot.parse_source_date("2026-10-25T10:00:00Z")
        self.assertEqual(parsed.hour, 17)

    def test_unknown_or_stale_source_date_is_rejected(self):
        current = datetime(2026, 10, 25, 12, 0, tzinfo=bot.TZ)
        self.assertFalse(bot.is_recent_source_date(None, current))
        self.assertFalse(bot.is_recent_source_date(current - timedelta(hours=73), current))
        self.assertFalse(bot.is_recent_source_date(current - timedelta(hours=25), current, urgent=True))

    def test_recent_source_date_limits_and_future_clock_tolerance(self):
        current = datetime(2026, 10, 25, 12, 0, tzinfo=bot.TZ)
        self.assertTrue(bot.is_recent_source_date(current - timedelta(hours=71), current))
        self.assertTrue(bot.is_recent_source_date(current - timedelta(hours=23), current, urgent=True))
        self.assertFalse(bot.is_recent_source_date(current + timedelta(minutes=20), current))
        self.assertTrue(bot.is_recent_source_date(current + timedelta(minutes=10), current))

    def test_suggest_command_collects_a_review_only_submission(self):
        uid = 1335496436
        state = {"pending_ad": {}, "pending_manual_post": {}, "manual_posts": []}
        admin = {"from": {"id": uid}, "chat": {"id": uid}, "text": "/suggest", "message_id": 10}
        with patch.object(bot, "send_dm"):
            bot.handle_admin_message(admin, state)
            self.assertTrue(state["pending_manual_post"]["awaiting_content"])
            submission = {
                "from": {"id": uid}, "chat": {"id": uid}, "message_id": 11,
                "text": "Repair works announced for Nha Trang road. https://example.com/road"
            }
            bot.handle_admin_message(submission, state)
        self.assertEqual(len(state["manual_posts"]), 1)
        self.assertEqual(state["manual_posts"][0]["status"], "pending")
        self.assertFalse(state["pending_manual_post"].get("awaiting_content", False))

    def test_newsqueue_lists_pending_item_id(self):
        uid = 1335496436
        state = {
            "pending_ad": {}, "pending_manual_post": {},
            "manual_posts": [{"id": "item-123", "status": "pending", "content": "Road repair notice"}]
        }
        admin = {"from": {"id": uid}, "chat": {"id": uid}, "text": "/newsqueue"}
        with patch.object(bot, "send_dm") as send_mock:
            bot.handle_admin_message(admin, state)
        sent = "\n".join(str(call.args) for call in send_mock.call_args_list)
        self.assertIn("item-123", sent)
        self.assertIn("/approve item-123", sent)

    def test_approve_manual_post_publishes_only_after_explicit_command(self):
        uid = 1335496436
        item = {
            "id": "item-234", "status": "pending",
            "type": "text",
            "content": "Road repair update\n\nThe city announced repair works.\nSource: https://example.com/road"
        }
        state = {
            "pending_ad": {}, "pending_manual_post": {}, "manual_posts": [item],
            "ads": [], "paused": False, "pause_until": "",
            "daily_counts": {}, "seen_links": [], "seen_titles": [],
            "seen_fingerprints": [], "published_news": []
        }
        admin = {"from": {"id": uid}, "chat": {"id": uid}, "text": "/approve item-234"}
        with patch.object(bot, "post_with_image", return_value={"message_id": 777}) as image_post_mock:
            with patch.object(bot, "send_dm"):
                with patch.object(bot.time, "sleep"):
                    bot.handle_admin_message(admin, state)
        self.assertEqual(item["status"], "published")
        self.assertEqual(item["published_message_id"], 777)
        image_post_mock.assert_called_once()
        posted_text = image_post_mock.call_args.args[0]
        self.assertEqual(state["published_news"][0]["title"], "Road repair update")
        self.assertEqual(state["published_news"][0]["link"], "https://example.com/road")
        self.assertIn("The city announced repair works.", posted_text)
        self.assertNotIn("Road repair update", posted_text)
        self.assertNotIn("example.com/road", posted_text)
        self.assertNotIn("<b>", posted_text)

    def test_manual_album_is_queued_and_not_published_until_approved(self):
        uid = 1335496436
        state = {"pending_manual_post": {"user_id": str(uid), "awaiting_content": True},
                 "pending_ad": {}, "manual_posts": [], "ads": [], "paused": False,
                 "pause_until": "", "daily_counts": {}}
        messages = [
            {"message_id": 501, "media_group_id": "album-1", "from": {"id": uid},
             "chat": {"id": uid}, "caption": "Road closure update",
             "photo": [{"file_id": "photo-1"}]},
            {"message_id": 502, "media_group_id": "album-1", "from": {"id": uid},
             "chat": {"id": uid}, "video": {"file_id": "video-1"}}
        ]
        with patch.object(bot, "send_dm"):
            with patch.object(bot, "api") as api_mock:
                bot.handle_admin_album(messages, state)
        self.assertEqual(len(state["manual_posts"]), 1)
        self.assertEqual(state["manual_posts"][0]["type"], "album")
        self.assertEqual(len(state["manual_posts"][0]["media_items"]), 2)
        self.assertEqual(state["manual_posts"][0]["status"], "pending")
        api_mock.assert_not_called()

    def test_approved_manual_album_publishes_all_media_items(self):
        uid = 1335496436
        item = {"id": "album-777", "status": "pending", "type": "album",
                "content": "Road closure update\n\nUse another street.",
                "media_items": [{"type": "photo", "file_id": "p1"},
                                {"type": "video", "file_id": "v2"}]}
        state = {"pending_ad": {}, "pending_manual_post": {}, "manual_posts": [item],
                 "ads": [], "paused": False, "pause_until": "", "daily_counts": {},
                 "seen_links": [], "seen_titles": [], "seen_fingerprints": [], "published_news": []}
        admin = {"from": {"id": uid}, "chat": {"id": uid}, "text": "/approve album-777"}
        with patch.object(bot, "api", return_value=[
            {"message_id": 800, "chat": {"id": -1001}},
            {"message_id": 801, "chat": {"id": -1001}}
        ]) as api_mock:
            with patch.object(bot, "send_dm"):
                with patch.object(bot.time, "sleep"):
                    bot.handle_admin_message(admin, state)
        self.assertEqual(item["status"], "published")
        self.assertEqual(item["published_message_ids"], [800, 801])
        self.assertEqual(api_mock.call_args.args[0], "sendMediaGroup")
        sent_media = json.loads(api_mock.call_args.args[1]["media"])
        self.assertEqual([m["media"] for m in sent_media], ["p1", "v2"])

    def test_reject_manual_post_never_publishes(self):
        uid = 1335496436
        item = {"id": "item-345", "status": "pending", "content": "Unverified claim"}
        state = {
            "pending_ad": {}, "pending_manual_post": {}, "manual_posts": [item],
            "ads": [], "paused": False, "pause_until": "", "daily_counts": {}
        }
        admin = {"from": {"id": uid}, "chat": {"id": uid}, "text": "/reject item-345"}
        with patch.object(bot, "send_dm"):
            with patch.object(bot, "post_text") as post_mock:
                bot.handle_admin_message(admin, state)
        self.assertEqual(item["status"], "rejected")
        post_mock.assert_not_called()

    def test_manual_photo_without_context_is_not_queued(self):
        uid = 1335496436
        state = {"pending_manual_post": {}, "manual_posts": []}
        msg = {
            "from": {"id": uid}, "chat": {"id": uid}, "message_id": 45,
            "photo": [{"file_id": "small"}, {"file_id": "large"}]
        }
        with patch.object(bot, "send_dm"):
            bot.capture_manual_post(msg, state, str(uid))
        self.assertEqual(state["manual_posts"], [])
        self.assertEqual(state["pending_manual_post"], {})

    def test_practical_local_story_is_kept(self):
        self.assertTrue(bot.is_relevant_news(
            "Khánh Hòa cảnh báo mưa lớn tại Nha Trang",
            "Chính quyền thông báo nguy cơ ngập ở một số tuyến đường."
        ))

    def test_image_search_matches_russian_translated_topics(self):
        self.assertIn("storm", bot.news_image_query("Сильный дождь и шторм в Нячанге"))
        self.assertIn("school", bot.news_image_query("Новые правила для школьников в Нячанге"))
        self.assertIn("hospital", bot.news_image_query("Изменения в больницах и клиниках"))
        self.assertIn("road traffic", bot.news_image_query("Ремонт дороги и новые ограничения движения"))
        self.assertIn("immigration", bot.news_image_query("Новые правила визы для иностранцев"))

    def test_urgent_terms_match_whole_words(self):
        self.assertTrue(bot.is_urgent_news("Cảnh báo bão lớn tại Nha Trang"))
        self.assertFalse(bot.is_urgent_news("Một nghệ sĩ nổi tiếng đến Nha Trang"))

    def test_commons_image_includes_license_and_author_credit(self):
        response = {
            "query": {
                "pages": {
                    "123": {
                        "fullurl": "https://commons.wikimedia.org/wiki/File:road.jpg",
                        "imageinfo": [{
                            "thumburl": "https://upload.wikimedia.org/road-thumb.jpg",
                            "extmetadata": {
                                "Artist": {"value": "Jane Doe"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"}
                            }
                        }]
                    }
                }
            }
        }
        with patch.object(bot, "get_url", return_value=json.dumps(response).encode("utf-8")):
            image = bot.commons_image("Nha Trang public services")
        self.assertIsNotNone(image)
        self.assertEqual(image["url"], "https://upload.wikimedia.org/road-thumb.jpg")
        self.assertIn("Jane Doe", image["credit_html"])
        self.assertIn("CC BY-SA 4.0", image["credit_html"])
        self.assertIn("https://creativecommons.org/licenses/by-sa/4.0/", image["credit_html"])

    def test_commons_noncommercial_license_is_rejected_for_monetized_channel(self):
        response = {"query": {"pages": {"123": {
            "fullurl": "https://commons.wikimedia.org/wiki/File:noncommercial.jpg",
            "imageinfo": [{"thumburl": "https://upload.wikimedia.org/noncommercial.jpg",
                           "extmetadata": {
                               "Artist": {"value": "Jane Doe"},
                               "LicenseShortName": {"value": "CC BY-NC-SA 4.0"},
                               "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-nc-sa/4.0/"}
                           }}]
        }}}}
        with patch.object(bot, "get_url", return_value=json.dumps(response).encode("utf-8")):
            self.assertIsNone(bot.commons_image("Nha Trang public services"))

    def test_commons_image_without_identifiable_license_is_rejected(self):
        response = {
            "query": {
                "pages": {
                    "123": {
                        "fullurl": "https://commons.wikimedia.org/wiki/File:unknown.jpg",
                        "imageinfo": [{
                            "thumburl": "https://upload.wikimedia.org/unknown.jpg",
                            "extmetadata": {}
                        }]
                    }
                }
            }
        }
        with patch.object(bot, "get_url", return_value=json.dumps(response).encode("utf-8")):
            self.assertIsNone(bot.commons_image("Nha Trang public services"))

    def test_irrelevant_local_sports_story_is_rejected(self):
        self.assertFalse(bot.is_relevant_news(
            "Khánh Hòa gặp Cần Thơ trong trận đấu bóng đá",
            "Bình luận trận đấu và đội hình trước giải."
        ))

    def test_generic_local_mention_without_practical_value_is_rejected(self):
        self.assertFalse(bot.is_relevant_news(
            "Một nghệ sĩ nổi tiếng đến Nha Trang",
            "Thông tin giải trí chung về một sự kiện, không có hướng dẫn hoặc hệ quả thiết thực."
        ))

    def test_short_word_fragment_does_not_trigger_false_urgent_story(self):
        self.assertFalse(bot.is_urgent_news("Một nghệ sĩ nổi tiếng đến Nha Trang"))

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

    def test_long_article_paragraphs_are_preserved(self):
        html_article = "<p>First useful paragraph.</p><p>Second paragraph with more facts.</p>"
        self.assertEqual(
            bot.clean_multiline(html_article),
            "First useful paragraph.\n\nSecond paragraph with more facts."
        )

    def test_telegram_transport_errors_do_not_leak_bot_token(self):
        secret = "BOT_TOKEN_SHOULD_NOT_LEAK"
        with patch.object(bot, "TOKEN", secret):
            with patch.object(bot.urllib.request, "urlopen", side_effect=OSError("failed https://api.telegram.org/bot" + secret + "/getUpdates")):
                with self.assertRaises(RuntimeError) as raised:
                    bot.api("getUpdates")
        self.assertNotIn(secret, str(raised.exception))

    def test_split_long_text_keeps_all_words(self):
        original = " ".join(["thongtin"] * 12000)
        pieces = bot.split_html_text(original, limit=3900)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece) <= 3900 for piece in pieces))
        self.assertEqual(" ".join(pieces).split(), original.split())

    def test_public_news_helper_attaches_the_supplied_article_image(self):
        post = "Полезная новость без заголовка. Подробности для жителей Нячанга."
        response = {"message_id": 100, "chat": {"id": -1001}}
        with patch.object(bot, "commons_image") as commons_mock, \
             patch.object(bot, "api", return_value=response) as api_mock, \
             patch.object(bot, "post_text") as text_mock:
            bot.post_with_image(post, "https://cdn.publisher.example/road-photo.jpg")
        self.assertEqual(api_mock.call_args.args[0], "sendPhoto")
        self.assertEqual(api_mock.call_args.args[1]["photo"], "https://cdn.publisher.example/road-photo.jpg")
        self.assertIn("Полезная новость", api_mock.call_args.args[1]["caption"])
        commons_mock.assert_not_called()
        text_mock.assert_not_called()
        self.assertNotIn("publisher.example/story", api_mock.call_args.args[1]["caption"])

    def test_long_photo_caption_preserves_continuation(self):
        post = ("Полезная информация для жителей Нячанга. " * 45) + "\n\nUNIQUE_END_MARKER"
        response = {"message_id": 100, "chat": {"id": -1001}}
        with patch.object(bot, "commons_image") as commons_mock, \
             patch.object(bot, "api", return_value=response) as api_mock, \
             patch.object(bot, "post_text", return_value={"message_id": 101}) as text_mock:
            bot.post_with_image(post, "https://cdn.publisher.example/road-photo.jpg")
        caption = api_mock.call_args.args[1]["caption"]
        self.assertLessEqual(len(caption), 950)
        self.assertGreaterEqual(text_mock.call_count, 1)
        self.assertIn("UNIQUE_END_MARKER", "\n".join(c.args[0] for c in text_mock.call_args_list))
        commons_mock.assert_not_called()

    def test_extract_article_image_prefers_open_graph_image(self):
        page = (
            '<html><head>'
            '<meta name="twitter:image" content="/media/social.jpg">'
            '<meta property="og:image" content="https://cdn.publisher.example/news/bridge.jpg">'
            '</head><body><article><img src="/media/thumbnail.jpg" width="120" height="80"></article></body></html>'
        )
        self.assertEqual(
            bot.extract_article_image(page, "https://publisher.example/story"),
            "https://cdn.publisher.example/news/bridge.jpg"
        )

    def test_extract_article_image_resolves_relative_url(self):
        page = '<meta property="og:image" content="/photos/road.jpg">'
        self.assertEqual(
            bot.extract_article_image(page, "https://publisher.example/news/road"),
            "https://publisher.example/photos/road.jpg"
        )

    def test_extract_rss_image_reads_media_content(self):
        xml = (
            '<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>'
            '<title>Road closure</title><link>https://publisher.example/news</link>'
            '<media:content url="https://cdn.publisher.example/road.jpg" type="image/jpeg"/>'
            '</item></channel></rss>'
        )
        item = bot.ET.fromstring(xml).find(".//item")
        self.assertEqual(
            bot.extract_rss_image(item, "https://publisher.example/news"),
            "https://cdn.publisher.example/road.jpg"
        )



    def test_morning_routine_queues_greeting_at_six_with_reaction_choices(self):
        current = datetime(2026, 10, 26, 6, 2, tzinfo=bot.TZ)
        state = {"published_news": [], "daily_counts": {}, "last_slots": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.routine_posts(state)
        queue_mock.assert_called_once()
        candidate = queue_mock.call_args.args[1]
        self.assertIn("Доброе утро", candidate["body"])
        self.assertIn("👍", candidate["body"])
        self.assertIn("❤️", candidate["body"])
        self.assertIn("🙏", candidate["body"])
        self.assertIn("image_query", candidate)
        self.assertEqual(candidate["image_query"], "Nha Trang Vietnam sunrise beach promenade morning light")
        self.assertEqual(queue_mock.call_args.kwargs["slot_key"], "review_morning")
        mark_mock.assert_called_once_with(state, "review_morning")

    def test_night_routine_queues_goodnight_and_interaction(self):
        current = datetime(2026, 10, 26, 22, 32, tzinfo=bot.TZ)
        state = {"published_news": [], "daily_counts": {}, "last_slots": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "get_url", side_effect=OSError("offline")), \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.routine_posts(state)
        queue_mock.assert_called_once()
        candidate = queue_mock.call_args.args[1]
        self.assertIn("Доброй ночи", candidate["body"])
        self.assertIn("выбери реакцию", candidate["body"])
        self.assertIn("❤️", candidate["body"])
        self.assertIn("🙏", candidate["body"])
        self.assertIn("🤣", candidate["body"])
        self.assertIn("image_query", candidate)
        self.assertEqual(candidate["image_query"], "Nha Trang Vietnam night city lights beach coastline")
        self.assertEqual(queue_mock.call_args.kwargs["slot_key"], "review_night")
        mark_mock.assert_called_once_with(state, "review_night")

    def test_morning_weather_is_planned_for_eight_not_six_thirty(self):
        early = datetime(2026, 10, 26, 6, 32, tzinfo=bot.TZ)
        state = {"daily_counts": {}, "last_slots": {}, "published_news": []}
        with patch.object(bot, "now", return_value=early), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "get_url") as get_mock, \
             patch.object(bot, "queue_review_item") as queue_mock:
            bot.weather_post(state, "morning")
        get_mock.assert_not_called()
        queue_mock.assert_not_called()

        current = datetime(2026, 10, 26, 8, 2, tzinfo=bot.TZ)
        forecast = {
            "current": {"temperature_2m": 28, "apparent_temperature": 32, "weather_code": 2},
            "daily": {
                "weather_code": [2, 3],
                "temperature_2m_min": [25, 26],
                "temperature_2m_max": [31, 30],
                "precipitation_probability_max": [20, 55]
            }
        }
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "get_url", return_value=json.dumps(forecast).encode("utf-8")), \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.weather_post(state, "morning")
        queue_mock.assert_called_once()
        candidate = queue_mock.call_args.args[1]
        self.assertIn("Сейчас в Нячанге", candidate["body"])
        self.assertIn("Сегодня ожидается", candidate["body"])
        self.assertNotIn("<b>", candidate["body"])
        self.assertIn("image_query", candidate)
        self.assertEqual(queue_mock.call_args.kwargs["slot_key"], "weather_morning")
        mark_mock.assert_called_once_with(state, "weather_morning")



    def test_heavy_rain_forecast_selects_a_rainy_vietnam_photo_query(self):
        current = datetime(2026, 10, 26, 8, 0, tzinfo=bot.TZ)
        forecast = {
            "current": {"temperature_2m": 28, "apparent_temperature": 32, "weather_code": 61},
            "daily": {
                "weather_code": [65, 82],
                "temperature_2m_min": [25, 26],
                "temperature_2m_max": [31, 30],
                "precipitation_probability_max": [85, 90]
            }
        }
        state = {"daily_counts": {}, "last_slots": {}, "published_news": []}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "get_url", return_value=json.dumps(forecast).encode("utf-8")), \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock, \
             patch.object(bot, "mark_slot"):
            bot.weather_post(state, "morning")
        candidate = queue_mock.call_args.args[1]
        self.assertIn("heavy rain", candidate["image_query"].casefold())
        self.assertIn("Вероятность дождя высокая", candidate["body"])

    def test_google_hosted_thumbnails_are_rejected(self):
        self.assertIsNone(bot.normalize_image_url("https://encrypted-tbn0.gstatic.com/images?q=test"))
        self.assertIsNone(bot.normalize_image_url("https://news.google.com/images/photo.jpg"))
        self.assertEqual(
            bot.normalize_image_url("https://cdn.publisher.example/news/photo.jpg"),
            "https://cdn.publisher.example/news/photo.jpg"
        )

    def test_queue_review_item_uses_a_fifteen_minute_timer_and_persists_candidate(self):
        current = datetime(2026, 10, 26, 10, 0, tzinfo=bot.TZ)
        state = {"pending_news_review": {}, "review_editing": {}}
        candidate = {
            "kind": "news", "kind_label": "Новость Нячанга",
            "title": "Road repair update", "source_title": "Road repair update",
            "body": "The city announced road repair works in Nha Trang.",
            "link": "https://publisher.example/road",
            "image_url": "https://cdn.publisher.example/road.jpg",
            "image_query": "Nha Trang Vietnam road traffic"
        }
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "send_review_preview", return_value=True):
            self.assertTrue(bot.queue_review_item(state, candidate))
        queued = state["pending_news_review"]
        self.assertEqual(queued["status"], "pending_review")
        self.assertEqual(bot.parse_dt_strict(queued["planned_at"]), current + timedelta(minutes=15))
        self.assertEqual(queued["link"], "https://publisher.example/road")
        self.assertEqual(state["review_editing"], {})

    def test_review_timer_does_not_publish_early(self):
        current = datetime(2026, 10, 26, 10, 0, tzinfo=bot.TZ)
        state = {"pending_news_review": {
            "id": "rv-test", "status": "pending_review",
            "planned_at": (current + timedelta(minutes=15)).isoformat(),
            "preview_messages": []
        }, "paused": False, "pause_until": "", "last_slots": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "publish_review_item") as publish_mock:
            bot.process_news_review(state)
        publish_mock.assert_not_called()
        self.assertEqual(state["pending_news_review"]["id"], "rv-test")



    def test_fixed_routine_publication_tolerates_subminute_scheduler_jitter(self):
        planned = datetime(2026, 10, 26, 8, 0, 30, tzinfo=bot.TZ)
        current = datetime(2026, 10, 26, 8, 0, 5, tzinfo=bot.TZ)
        state = {
            "pending_news_review": {
                "id": "rv-weather",
                "status": "pending_review",
                "planned_at": planned.isoformat(),
                "slot_key": "weather_morning"
            },
            "paused": False, "pause_until": "", "last_slots": {}
        }
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "publish_review_item", return_value={"message_id": 1}) as publish_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.process_news_review(state)
        publish_mock.assert_called_once_with(state, state["pending_news_review"])
        mark_mock.assert_called_once_with(state, "weather_morning")

    def test_due_review_publishes_once_and_updates_published_news_history(self):
        uid = 1335496436
        current = datetime(2026, 10, 26, 10, 30, tzinfo=bot.TZ)
        item = {
            "id": "rv-publish-1", "kind": "news", "status": "pending_review",
            "title": "Ремонт дороги в Нячанге",
            "source_title": "Nha Trang announces road repairs",
            "body": "Местные власти объявили о ремонте дороги в Нячанге. Жителям стоит учитывать изменения движения.",
            "link": "https://publisher.example/road-repair",
            "source_published_at": current.isoformat(),
            "image_file_id": "reviewed-photo-file",
            "image_query": "Nha Trang Vietnam road repairs",
            "planned_at": (current - timedelta(seconds=1)).isoformat(),
            "preview_messages": [],
            "urgent": False
        }
        state = {
            "pending_news_review": item, "review_editing": {},
            "seen_links": [], "seen_titles": [], "seen_fingerprints": [],
            "published_news": [], "daily_counts": {}, "last_slots": {}
        }
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "post_with_image", return_value={"message_id": 990}) as post_mock, \
             patch.object(bot, "delete_review_previews"), \
             patch.object(bot, "send_dm"), \
             patch.object(bot, "bump_today"):
            bot.publish_review_item(state, item)
        post_mock.assert_called_once()
        posted_text = post_mock.call_args.args[0]
        self.assertIn("Местные власти объявили", posted_text)
        self.assertNotIn("Nha Trang announces road repairs", posted_text)
        self.assertNotIn("publisher.example/road-repair", posted_text)
        self.assertEqual(post_mock.call_args.kwargs["image_file_id"], "reviewed-photo-file")
        self.assertEqual(state["pending_news_review"], {})
        self.assertEqual(state["published_news"][0]["link"], "https://publisher.example/road-repair")
        self.assertEqual(state["published_news"][0]["summary"], item["body"])
        self.assertIn("https://publisher.example/road-repair", state["seen_links"])

    def test_review_timer_publishes_at_due_time_and_marks_routine_slot(self):
        current = datetime(2026, 10, 26, 8, 0, tzinfo=bot.TZ)
        item = {
            "id": "rv-weather-1", "kind": "weather_morning", "status": "pending_review",
            "planned_at": (current - timedelta(seconds=1)).isoformat(),
            "slot_key": "weather_morning"
        }
        state = {"pending_news_review": item, "paused": False, "pause_until": "", "last_slots": {}}

        def fake_publish(saved_state, queued_item):
            saved_state["pending_news_review"] = {}
            return {"message_id": 991}

        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "publish_review_item", side_effect=fake_publish) as publish_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.process_news_review(state)
        publish_mock.assert_called_once_with(state, item)
        mark_mock.assert_called_once_with(state, "weather_morning")
        self.assertEqual(state["pending_news_review"], {})

    def test_rejected_review_is_never_queued_again_from_same_source(self):
        uid = 1335496436
        item = {
            "id": "rv-reject-1", "kind": "news", "status": "pending_review",
            "link": "https://publisher.example/rejected",
            "source_title": "Repair work announced for Nha Trang road",
            "title": "Ремонт дороги в Нячанге",
            "planned_at": bot.iso(bot.now() + timedelta(minutes=10)),
            "preview_messages": []
        }
        state = {
            "pending_news_review": item, "review_editing": {},
            "rejected_news_links": [], "rejected_news_fingerprints": [],
            "rejected_news_titles": [], "seen_links": [], "seen_titles": [],
            "seen_fingerprints": [], "last_slots": {}, "ads": []
        }
        callback = {
            "id": "callback-review-1",
            "from": {"id": uid},
            "data": "review:reject:rv-reject-1",
            "message": {"chat": {"id": uid}}
        }
        with patch.object(bot, "delete_review_previews"), \
             patch.object(bot, "send_dm"), \
             patch.object(bot, "api"):
            bot.handle_callback(callback, state)
        self.assertEqual(state["pending_news_review"], {})
        self.assertIn("https://publisher.example/rejected", state["rejected_news_links"])
        self.assertTrue(bot.is_duplicate_news(state, item["source_title"], item["link"]))

    def test_editing_review_resets_the_fifteen_minute_timer(self):
        uid = 1335496436
        current = datetime(2026, 10, 26, 10, 0, tzinfo=bot.TZ)
        item = {
            "id": "rv-edit-1", "kind": "news", "status": "pending_review",
            "body": "Old text for the planned post.", "reaction_line": "👍 — полезно.",
            "image_file_id": "photo-file-reviewed",
            "planned_at": bot.iso(current + timedelta(minutes=2)),
            "preview_messages": []
        }
        state = {"pending_news_review": item, "review_editing": {"id": item["id"], "user_id": str(uid)}}
        message = {"from": {"id": uid}, "chat": {"id": uid}, "text": "Updated text for the planned post."}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "delete_review_previews"), \
             patch.object(bot, "send_review_preview", return_value=True), \
             patch.object(bot, "send_dm"):
            bot.handle_admin_message(message, state)
        self.assertEqual(item["body"], "Updated text for the planned post.")
        self.assertEqual(item["reaction_line"], "")
        self.assertEqual(item["image_file_id"], "photo-file-reviewed")
        self.assertEqual(bot.parse_dt_strict(item["planned_at"]), current + timedelta(minutes=15))
        self.assertEqual(state["review_editing"], {})


    def test_review_time_button_sets_time_editing_mode(self):
        uid = 1335496436
        item = {
            "id": "rv-time-1", "status": "pending_review",
            "planned_at": bot.iso(datetime(2026, 10, 26, 11, 0, tzinfo=bot.TZ))
        }
        state = {"pending_news_review": item, "review_editing": {}}
        callback = {
            "id": "callback-time-1",
            "from": {"id": uid},
            "message": {"chat": {"id": uid}},
            "data": "review:time:rv-time-1"
        }
        with patch.object(bot, "now", return_value=datetime(2026, 10, 26, 10, 0, tzinfo=bot.TZ)), \
             patch.object(bot, "send_dm") as dm_mock, \
             patch.object(bot, "api"):
            bot.handle_callback(callback, state)
        self.assertEqual(state["review_editing"]["mode"], "time")
        self.assertEqual(state["review_editing"]["id"], "rv-time-1")
        self.assertIn("HH:MM", dm_mock.call_args.args[1])

    def test_review_time_edit_updates_saved_schedule_and_status_message(self):
        uid = 1335496436
        current = datetime(2026, 10, 26, 10, 0, tzinfo=bot.TZ)
        item = {
            "id": "rv-time-2", "kind": "news", "kind_label": "Новость Нячанга",
            "body": "A useful article body.",
            "planned_at": bot.iso(current + timedelta(minutes=15)),
            "preview_messages": [{"chat_id": uid, "message_id": 222}],
            "review_status_messages": [{"chat_id": uid, "message_id": 223}]
        }
        state = {"pending_news_review": item, "review_editing": {
            "id": "rv-time-2", "user_id": str(uid), "mode": "time"
        }}
        message = {"from": {"id": uid}, "chat": {"id": uid}, "text": "11:45"}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "api") as api_mock, \
             patch.object(bot, "send_dm") as dm_mock:
            bot.handle_admin_message(message, state)
        self.assertEqual(bot.parse_dt_strict(item["planned_at"]), current.replace(hour=11, minute=45))
        self.assertEqual(state["review_editing"], {})
        self.assertEqual(api_mock.call_args.args[0], "editMessageText")
        self.assertEqual(api_mock.call_args.args[1]["message_id"], 223)
        self.assertIn("11:45", api_mock.call_args.args[1]["text"])
        self.assertIn("изменено", dm_mock.call_args.args[1])


    def test_morning_routine_catches_up_when_github_schedule_runs_late(self):
        current = datetime(2026, 10, 26, 6, 42, tzinfo=bot.TZ)
        state = {"published_news": [], "daily_counts": {}, "last_slots": {}, "pending_news_review": {}}
        queued = {}
        def fake_queue(s, item, planned_at=None, slot_key=None):
            queued.update({"item": item, "planned_at": planned_at, "slot_key": slot_key})
            return True
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "queue_review_item", side_effect=fake_queue), \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.routine_posts(state)
        self.assertEqual(queued["slot_key"], "review_morning")
        self.assertIn("Доброе утро", queued["item"]["body"])
        self.assertEqual(queued["planned_at"], current + timedelta(minutes=15))
        mark_mock.assert_called_once_with(state, "review_morning")

    def test_morning_weather_catches_up_after_a_delayed_workflow_run(self):
        current = datetime(2026, 10, 26, 8, 42, tzinfo=bot.TZ)
        state = {"published_news": [], "daily_counts": {}, "last_slots": {}, "pending_news_review": {}}
        forecast = {
            "current": {"temperature_2m": 28, "apparent_temperature": 32, "weather_code": 2},
            "daily": {
                "weather_code": [2, 3],
                "temperature_2m_min": [25, 26],
                "temperature_2m_max": [31, 30],
                "precipitation_probability_max": [20, 55]
            }
        }
        queued = {}
        def fake_queue(s, item, planned_at=None, slot_key=None):
            queued.update({"item": item, "planned_at": planned_at, "slot_key": slot_key})
            return True
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "get_url", return_value=json.dumps(forecast).encode("utf-8")), \
             patch.object(bot, "queue_review_item", side_effect=fake_queue), \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.weather_post(state, "morning")
        self.assertEqual(queued["slot_key"], "weather_morning")
        self.assertIn("Сейчас в Нячанге", queued["item"]["body"])
        self.assertEqual(queued["planned_at"], current + timedelta(minutes=15))
        mark_mock.assert_called_once_with(state, "weather_morning")

    def test_limit_news_body_is_concise_and_removes_urls(self):
        source = ("Первая важная информация для жителей города. " * 30) + " https://publisher.example/story "
        result = bot.limit_news_body(source, max_chars=600)
        self.assertLessEqual(len(result), 600)
        self.assertNotIn("publisher.example", result)
        self.assertTrue(result.endswith("…") or result.endswith((".", "!", "?")))

    def test_publish_news_queues_article_image_without_generated_headline_or_source_link(self):
        current = datetime(2026, 10, 26, 10, 15, tzinfo=bot.TZ)
        description = (
            "The local authority announced road repairs in Nha Trang. "
            "The project will improve the road surface and pedestrian crossings in the neighbourhood. "
            "Work is expected to proceed in stages while residents can still reach nearby homes and shops. "
            "The authority will publish further details about the road repair schedule."
        )
        rss = (
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><item>'
            '<title>Nha Trang công bố sửa chữa đường tại khu dân cư</title>'
            '<description>' + description + '</description>'
            '<link>https://publisher.example/story-123</link>'
            '<pubDate>Mon, 26 Oct 2026 03:00:00 GMT</pubDate>'
            '</item></channel></rss>'
        ).encode("utf-8")
        state = {
            "seen_links": [], "seen_titles": [], "seen_fingerprints": [],
            "published_news": [], "ads": [], "paused": False, "pause_until": "",
            "daily_counts": {}, "last_slots": {}, "last_news_hour": ""
        }

        def fake_translate(value):
            if value.startswith("Nha Trang công bố sửa chữa"):
                return "Ремонт дороги в жилом районе Нячанга"
            return (
                "Местные власти объявили о ремонте дороги в Нячанге. "
                "Проект улучшит дорожное покрытие и пешеходные переходы в районе. "
                "Работы планируют проводить поэтапно, при этом жители смогут добираться до домов и магазинов. "
                "Дополнительные сведения о графике ремонта опубликуют позднее."
            )

        article_image = "https://cdn.publisher.example/news/flooded-road.jpg"
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "FEEDS", ["https://fake-feed.example/rss"]), \
             patch.object(bot, "get_url", return_value=rss), \
             patch.object(bot, "get_url_with_final_url", return_value=("https://publisher.example/story-123", b"<html></html>")), \
             patch.object(bot, "parse_source_date", return_value=current), \
             patch.object(bot, "translate", side_effect=fake_translate), \
             patch.object(bot, "extract_article_image", return_value=article_image), \
             patch.object(bot, "commons_image") as commons_mock, \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock:
            bot.publish_news(state)

        commons_mock.assert_not_called()
        queue_mock.assert_called_once()
        candidate = queue_mock.call_args.args[1]
        self.assertIn("Местные власти объявили", candidate["body"])
        self.assertNotIn("Ремонт дороги в жилом районе Нячанга", candidate["body"])
        self.assertNotIn("publisher.example/story-123", candidate["body"])
        self.assertNotIn("<b>", candidate["body"])
        self.assertEqual(candidate["image_url"], article_image)
        self.assertEqual(candidate["link"], "https://publisher.example/story-123")
        self.assertEqual(state["published_news"], [])

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

    def test_invalid_existing_ads_field_is_not_replaced_with_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bot_state.json")
            original = {"ads": {"legacy_payload": "keep-this"}, "seen_links": []}
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(original, stream)
            with patch.object(bot, "STATE_FILE", path):
                with self.assertRaises(RuntimeError):
                    bot.load_state()
            with open(path, encoding="utf-8") as stream:
                saved = json.load(stream)
            self.assertEqual(saved["ads"], original["ads"])

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

    def test_validate_ad_draft_rejects_photo_caption_over_limit_before_scheduling(self):
        ad = {"type": "photo", "file_id": "file-photo", "text": "x" * 1200}
        with self.assertRaises(ValueError):
            bot.validate_ad_draft(ad)

    def test_validate_ad_draft_accepts_long_text_only_ads(self):
        ad = {"text": " ".join(["advertising"] * 1200)}
        bot.validate_ad_draft(ad)

    def test_long_text_ad_tracks_every_message_for_expiry(self):
        sent = []
        def fake_post(text):
            result = {"message_id": 501 + len(sent), "chat": {"id": -1001}}
            sent.append(text)
            return result
        ad = {"text": " ".join(["advertising"] * 1200)}
        with patch.object(bot, "post_text", side_effect=fake_post) as post_mock:
            with patch.object(bot.time, "sleep"):
                result = bot.send_ad_content(ad)
        self.assertGreater(post_mock.call_count, 1)
        expected_ids = list(range(501, 501 + post_mock.call_count))
        self.assertEqual(result["message_id"], 501)
        self.assertEqual(result["_message_ids"], expected_ids)
        self.assertEqual(result["chat"]["id"], -1001)
        self.assertTrue(all(len(text) <= 3900 for text in sent))

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

    def test_editing_multipart_text_ad_replaces_all_parts(self):
        uid = 1335496436
        ad = {
            "id": "ad-old", "text": "Old ad", "message_id": 10,
            "message_ids": [10, 11, 12], "chat_id": -1001,
            "top_hours": 1, "life_hours": 24
        }
        state = {
            "pending_ad": {}, "pending_manual_post": {}, "ads": [ad],
            "daily_counts": {}, "last_slots": {}, "paused": False, "pause_until": ""
        }
        sent = []
        def fake_post(text):
            result = {"message_id": 100 + len(sent), "chat": {"id": -1001}}
            sent.append(text)
            return result
        admin = {
            "from": {"id": uid}, "chat": {"id": uid},
            "text": "/editad ad-old | " + "New advertising content " * 500
        }
        with patch.object(bot, "post_text", side_effect=fake_post) as post_mock:
            with patch.object(bot, "api", return_value={}) as api_mock:
                with patch.object(bot, "send_dm"):
                    with patch.object(bot.time, "sleep"):
                        bot.handle_admin_message(admin, state)
        self.assertGreater(post_mock.call_count, 1)
        self.assertEqual(ad["message_id"], 100)
        self.assertEqual(ad["message_ids"], list(range(100, 100 + post_mock.call_count)))
        self.assertEqual(ad["text"].startswith("New advertising content"), True)
        deleted = [call.args[1]["message_id"] for call in api_mock.call_args_list if call.args[0] == "deleteMessage"]
        self.assertEqual(deleted, [10, 11, 12])
        self.assertEqual(ad.get("stale_message_ids"), [])

    def test_expiring_ad_deletes_all_album_messages(self):
        ad = {"chat_id": -1001, "message_id": 201, "message_ids": [201, 202, 203]}
        with patch.object(bot, "api", return_value={}) as api_mock:
            bot.delete_ad_message(ad)
        deleted = [
            c.args[0] for c in api_mock.call_args_list
            if c.args[0] == "deleteMessage"
        ]
        self.assertEqual(len(deleted), 3)

    def test_admin_can_pin_published_ad(self):
        uid = "1335496436"
        state = {"ads": [{"id": "ad-1", "message_id": 301, "message_ids": [301, 302], "chat_id": -1001}]}
        callback = {
            "id": "callback-1",
            "from": {"id": int(uid)},
            "data": "ad_pin:ad-1",
            "message": {"chat": {"id": int(uid)}}
        }
        with patch.object(bot, "api", return_value={}) as api_mock:
            with patch.object(bot, "send_dm") as send_dm_mock:
                bot.handle_callback(callback, state)
        self.assertEqual(api_mock.call_args_list[0].args[0], "pinChatMessage")
        self.assertEqual(api_mock.call_args_list[0].args[1]["message_id"], 301)
        send_dm_mock.assert_called()
        self.assertIn("📌 Закрепить", str(bot.ad_actions("ad-1")))

    def test_scheduled_ad_failure_notifies_owner_once_and_retries(self):
        uid = "1335496436"
        ad = {
            "id": "scheduled-1", "created_by": uid,
            "scheduled_at": bot.iso(bot.now() - timedelta(minutes=2)),
            "top_hours": 1, "life_hours": 24, "text": "Scheduled ad"
        }
        state = {"ads": [ad]}
        with patch.object(bot, "publish_ad", side_effect=RuntimeError("test publish failure")) as publish_mock:
            with patch.object(bot, "send_dm") as send_mock:
                bot.process_ad_timers(state)
                bot.process_ad_timers(state)
        self.assertEqual(publish_mock.call_count, 2)
        self.assertTrue(ad["_publish_error_notified"])
        self.assertEqual(send_mock.call_count, 1)

    def test_ad_expiry_failure_notifies_owner_once(self):
        uid = "1335496436"
        ad = {
            "id": "expiry-1", "created_by": uid, "message_id": 900,
            "message_ids": [900], "chat_id": -1001,
            "expires_at": bot.iso(bot.now() - timedelta(minutes=2))
        }
        state = {"ads": [ad]}
        with patch.object(bot, "delete_ad_message", side_effect=RuntimeError("test delete failure")) as delete_mock:
            with patch.object(bot, "send_dm") as send_mock:
                bot.process_ad_timers(state)
                bot.process_ad_timers(state)
        self.assertEqual(delete_mock.call_count, 2)
        self.assertTrue(ad["_expiry_error_notified"])
        self.assertEqual(send_mock.call_count, 1)

    def test_invalid_ad_timestamps_never_publish_or_delete_automatically(self):
        state = {
            "ads": [
                {"id": "bad-schedule", "scheduled_at": "not-a-date", "top_hours": 1, "life_hours": 24},
                {"id": "bad-expiry", "message_id": 301, "expires_at": "not-a-date"},
            ]
        }
        with patch.object(bot, "publish_ad") as publish_mock:
            with patch.object(bot, "delete_ad_message") as delete_mock:
                bot.process_ad_timers(state)
        publish_mock.assert_not_called()
        delete_mock.assert_not_called()

    def test_missing_album_item_does_not_block_expiry_cleanup(self):
        def fake_api(method, data=None):
            if method == "deleteMessage" and data.get("message_id") == 401:
                raise RuntimeError("Telegram API deleteMessage failed: Bad Request: message to delete not found")
            return {}
        ad = {"chat_id": -1001, "message_id": 401, "message_ids": [401, 402]}
        with patch.object(bot, "api", side_effect=fake_api):
            bot.delete_ad_message(ad)


if __name__ == "__main__":
    unittest.main()
