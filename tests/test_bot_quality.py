import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import call, patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-for-local-unit-tests")
os.environ.setdefault("ADMIN_USER_IDS", "111111111,222222222")
os.environ.setdefault("WEATHERAPI_KEY", "test-only-weather-key")
import bot  # noqa: E402


def make_weather_fixture(
    current_time,
    rain_today=20,
    rain_tomorrow=55,
    code_today=2,
    code_tomorrow=3,
    current_code=2,
    temperature=28,
):
    """Provider-neutral fixture for weather tests; every numeric value is explicit."""
    today = current_time.date()
    tomorrow = today + timedelta(days=1)
    times = []
    for offset in range(48):
        dt = datetime(today.year, today.month, today.day, tzinfo=bot.TZ) + timedelta(hours=offset)
        times.append(dt.isoformat(timespec="minutes"))
    return {
        "provider": "Open-Meteo",
        "provider_url": "https://open-meteo.com/",
        "retrieved_at": current_time.isoformat(),
        "current": {
            "temperature_2m": temperature,
            "apparent_temperature": temperature + 4,
            "weather_code": current_code,
            "wind_speed_10m": 12,
            "wind_gusts_10m": 20,
        },
        "hourly": {
            "time": times,
            "temperature_2m": [temperature] * 48,
            "apparent_temperature": [temperature + 4] * 48,
            "precipitation_probability": [rain_today] * 24 + [rain_tomorrow] * 24,
            "precipitation": [0.0] * 48,
            "weather_code": [code_today] * 24 + [code_tomorrow] * 24,
            "wind_speed_10m": [12] * 48,
            "wind_gusts_10m": [20] * 48,
        },
        "daily": {
            "time": [today.isoformat(), tomorrow.isoformat()],
            "temperature_2m_min": [25, 26],
            "temperature_2m_max": [31, 30],
            "precipitation_probability_max": [rain_today, rain_tomorrow],
            "precipitation_sum": [0.0, 0.0],
            "weather_code": [code_today, code_tomorrow],
            "wind_gusts_10m_max": [20, 24],
        },
        "official_alerts": [],
    }


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
        uid = 111111111
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
        self.assertEqual(state["manual_posts"][0]["status"], "queued_for_review")
        self.assertFalse(state["pending_manual_post"].get("awaiting_content", False))

    def test_newsqueue_lists_pending_item_id(self):
        uid = 111111111
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
        uid = 111111111
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

    def test_manual_album_is_queued_through_editorial_preview(self):
        uid = 111111111
        state = {"pending_manual_post": {"user_id": str(uid), "awaiting_content": True},
                 "pending_ad": {}, "manual_posts": [], "ads": [], "paused": False,
                 "pause_until": "", "daily_counts": {}, "review_queue": [],
                 "pending_news_review": {}}
        messages = [
            {"message_id": 501, "media_group_id": "album-1", "from": {"id": uid},
             "chat": {"id": uid},
             "caption": "Закрытие дороги в центре Нячанга\nСегодня утром движение перекрыли для ремонта, водителям стоит использовать соседнюю улицу.",
             "photo": [{"file_id": "photo-1"}]},
            {"message_id": 502, "media_group_id": "album-1", "from": {"id": uid},
             "chat": {"id": uid}, "video": {"file_id": "video-1"}}
        ]
        with patch.object(bot, "send_dm") as dm_mock, \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock, \
             patch.object(bot, "api") as api_mock:
            bot.handle_admin_album(messages, state)
        self.assertEqual(len(state["manual_posts"]), 1)
        self.assertEqual(state["manual_posts"][0]["type"], "album")
        self.assertEqual(len(state["manual_posts"][0]["media_items"]), 2)
        self.assertEqual(state["manual_posts"][0]["status"], "queued_for_review")
        self.assertFalse(state["pending_manual_post"].get("awaiting_content", False))
        candidate = queue_mock.call_args.args[1]
        self.assertEqual(candidate["headline"], "Закрытие дороги в центре Нячанга")
        self.assertIn("Сегодня утром", candidate["body"])
        self.assertEqual(candidate["media_items"][0]["type"], "photo")
        self.assertEqual(candidate["media_items"][1]["type"], "video")
        api_mock.assert_not_called()
        self.assertTrue(any("редакторскую очередь" in str(call.args) for call in dm_mock.call_args_list))



    def test_approved_manual_album_publishes_all_media_items(self):
        uid = 111111111
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
        uid = 111111111
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
        uid = 111111111
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

    def test_long_photo_caption_is_blocked_without_text_only_continuation(self):
        post = ("Полезная информация для жителей Нячанга. " * 45) + "\n\nUNIQUE_END_MARKER"
        with patch.object(bot, "commons_image") as commons_mock, \
             patch.object(bot, "api") as api_mock, \
             patch.object(bot, "post_text") as text_mock:
            with self.assertRaises(bot.MediaCaptionTooLongError):
                bot.post_with_image(post, "https://cdn.publisher.example/road-photo.jpg")
        api_mock.assert_not_called()
        text_mock.assert_not_called()
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



    def test_morning_chat_prompt_is_integrated_into_weather_not_a_separate_post(self):
        current = datetime(2026, 10, 26, 6, 2, tzinfo=bot.TZ)
        state = {"published_news": [], "daily_counts": {}, "last_slots": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "queue_review_item") as queue_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.routine_posts(state)
        queue_mock.assert_not_called()
        mark_mock.assert_not_called()



    def test_night_routine_queues_name_only_game_with_no_reaction_prompt(self):
        current = datetime(2026, 10, 27, 22, 32, tzinfo=bot.TZ)
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
        self.assertEqual(candidate["headline"], "Открываем ночной чат!")
        self.assertIn("вам на", candidate["body"])
        self.assertIn("имена", candidate["body"])
        self.assertEqual(candidate["reaction_line"], "")
        self.assertIn("image_query", candidate)
        self.assertEqual(queue_mock.call_args.kwargs["slot_key"], "review_night")
        mark_mock.assert_called_once_with(state, "review_night")



    def test_morning_weather_is_planned_for_eight_not_six_thirty(self):
        early = datetime(2026, 10, 26, 6, 32, tzinfo=bot.TZ)
        state = {"daily_counts": {}, "last_slots": {}, "published_news": []}
        with patch.object(bot, "now", return_value=early), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "fetch_weather_data") as forecast_mock, \
             patch.object(bot, "queue_review_item") as queue_mock:
            bot.weather_post(state, "morning")
        forecast_mock.assert_not_called()
        queue_mock.assert_not_called()

        current = datetime(2026, 10, 26, 8, 2, tzinfo=bot.TZ)
        forecast = make_weather_fixture(current)
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "fetch_weather_data", return_value=forecast), \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.weather_post(state, "morning")
        queue_mock.assert_called_once()
        candidate = queue_mock.call_args.args[1]
        self.assertIn("Сейчас в Нячанге", candidate["body"])
        self.assertIn("Сегодня (26.10)", candidate["body"])
        self.assertIn("вероятность дождя", candidate["body"])
        self.assertIn("Обновлено:", candidate["body"])
        self.assertNotIn("<b>", candidate["body"])
        self.assertEqual(candidate["forecast_date"], "2026-10-26")
        self.assertEqual(queue_mock.call_args.kwargs["slot_key"], "weather_morning")
        mark_mock.assert_called_once_with(state, "weather_morning")




    def test_heavy_rain_forecast_selects_a_rainy_vietnam_photo_query(self):
        current = datetime(2026, 10, 26, 8, 0, tzinfo=bot.TZ)
        forecast = make_weather_fixture(current, rain_today=85, rain_tomorrow=90, code_today=65, code_tomorrow=82, current_code=61)
        state = {"daily_counts": {}, "last_slots": {}, "published_news": []}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "fetch_weather_data", return_value=forecast), \
             patch.object(bot, "queue_review_item", return_value=True) as queue_mock, \
             patch.object(bot, "mark_slot"):
            bot.weather_post(state, "morning")
        candidate = queue_mock.call_args.args[1]
        self.assertGreaterEqual(candidate["body"].count("%"), 1)
        self.assertRegex(candidate["image_query"].casefold(), r"rain|storm|downpour")
        self.assertIn("Vietnam", candidate["image_query"])




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
        uid = 111111111
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
        self.assertIn("ПОДПИСАТЬСЯ", posted_text)
        self.assertIn("ПРЕДЛОЖИТЬ НОВОСТЬ", posted_text)
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
        uid = 111111111
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
        uid = 111111111
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
        uid = 111111111
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
        uid = 111111111
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


    def test_morning_routine_catches_up_without_emitting_a_duplicate_greeting(self):
        current = datetime(2026, 10, 26, 6, 42, tzinfo=bot.TZ)
        state = {"published_news": [], "daily_counts": {}, "last_slots": {}, "pending_news_review": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "count_today", return_value=0), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "queue_review_item") as queue_mock, \
             patch.object(bot, "mark_slot") as mark_mock:
            bot.routine_posts(state)
        queue_mock.assert_not_called()
        mark_mock.assert_not_called()



    def test_morning_weather_catches_up_after_a_delayed_workflow_run(self):
        current = datetime(2026, 10, 26, 8, 42, tzinfo=bot.TZ)
        state = {"daily_counts": {}, "last_slots": {}, "published_news": [], "pending_news_review": {}}
        forecast = make_weather_fixture(current)
        queued = {}
        def fake_queue(s, item, planned_at=None, slot_key=None):
            queued.update({"item": item, "planned_at": planned_at, "slot_key": slot_key})
            return True
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "slot_once", return_value=False), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "fetch_weather_data", return_value=forecast), \
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

    def test_publish_news_queues_article_image_with_separate_headline_and_source_metadata(self):
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
        uid = 111111111
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
        uid = "111111111"
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
        uid = "111111111"
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
        uid = "111111111"
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



    def test_public_post_has_bold_separate_headline_and_only_approved_reactions(self):
        item = {
            "kind": "news",
            "headline": "Nha Trang changes the road access rules",
            "body": "Residents should check the new route before travelling through the district.",
            "reaction_line": "😂 — это смешно\n👍 — полезная информация",
            "link": "https://publisher.example/road-story",
            "source_url": "https://publisher.example/road-story",
            "source_published_at": "2026-10-27T09:00:00+07:00",
        }
        rendered = bot.review_post_text(item)
        self.assertTrue(rendered.startswith("<b>Nha Trang changes the road access rules</b>"))
        self.assertIn("Residents should check", rendered)
        self.assertNotIn("publisher.example/road-story", rendered)
        status = bot.review_status_text({
            **item, "kind_label": "Полезная новость", "id": "rv-test", "version": 1,
            "planned_at": "2026-10-27T10:00:00+07:00", "source_date_basis": "article_metadata"
        })
        self.assertIn('<a href="https://publisher.example/road-story">publisher.example</a>', status)
        self.assertIn("Дата источника", status)
        self.assertNotIn("<blockquote", rendered)
        self.assertNotIn("😂", rendered)
        self.assertIn("👍 — полезная информация", rendered)
        self.assertIn("ПОДПИСАТЬСЯ", rendered)
        self.assertIn("ПРЕДЛОЖИТЬ НОВОСТЬ", rendered)

    def test_reactions_reject_every_emoji_outside_the_allowlist(self):
        value = "😂 — нет\n🔥 — нет\n❤️ — да\n🙏 — да\n😱 — да\n👍 — да\n🤣 — да\n🙂 — нет"
        actual = bot.sanitize_reaction_line(value)
        self.assertEqual(actual.splitlines(), [
            "❤️ — да", "🙏 — да", "😱 — да", "👍 — да", "🤣 — да"
        ])

    def test_no_photo_means_no_public_text_only_fallback(self):
        post = "<b>Заголовок</b>\n\nПроверенный текст новости."
        with patch.object(bot, "commons_image", return_value=None), \
             patch.object(bot, "api") as api_mock, \
             patch.object(bot, "post_text") as text_mock:
            result = bot.post_with_image(post, image_url=None, image_query="Nha Trang road")
        self.assertIsNone(result)
        api_mock.assert_not_called()
        text_mock.assert_not_called()

    def test_article_date_metadata_is_used_when_rss_date_is_missing(self):
        page = (
            '<html><head>'
            '<meta property="article:published_time" content="2026-10-27T08:15:00+07:00">'
            '<meta property="og:updated_time" content="2026-10-27T08:45:00+07:00">'
            '</head><body></body></html>'
        )
        dates = bot.extract_article_date_candidates(page)
        self.assertEqual(len(dates), 2)
        current = datetime(2026, 10, 27, 9, 0, tzinfo=bot.TZ)
        recent = bot.extract_recent_article_date(page, current_time=current)
        self.assertIsNotNone(recent)
        self.assertEqual(recent.hour, 8)
        self.assertEqual(recent.minute, 45)

    def test_weather_alert_decision_requires_a_material_measurable_change(self):
        changed, reason = bot.weather_change_decision(
            {"rain_max": 40, "severe_code": None, "wind_max": 15},
            {"rain_max": 80, "severe_code": None, "wind_max": 20},
        )
        self.assertTrue(changed)
        self.assertIn("40%", reason)
        self.assertIn("80%", reason)
        unchanged, _ = bot.weather_change_decision(
            {"rain_max": None, "severe_code": None, "wind_max": None},
            {"rain_max": None, "severe_code": None, "wind_max": None},
        )
        self.assertFalse(unchanged)

    def test_weather_copy_does_not_turn_missing_rain_probability_into_zero(self):
        current = datetime(2026, 10, 27, 8, 0, tzinfo=bot.TZ)
        data = make_weather_fixture(current)
        data["daily"]["precipitation_probability_max"] = [None, None]
        data["hourly"]["precipitation_probability"] = [None] * 48
        headline, body, target = bot.format_weather_forecast(data, "morning", current)
        self.assertEqual(target, current.date())
        self.assertIn("вероятность дождя: нет данных", body)
        self.assertIn("Максимальная вероятность дождя за день: нет данных.", body)
        self.assertNotIn("0%", body)
        self.assertIn("Обновлено: 27.10.2026 08:00", body)
        self.assertTrue(headline.startswith("Погода в Нячанге"))

    def test_gemini_reaction_prompt_is_whitelisted_and_limited(self):
        line = bot.format_ai_reaction_prompt([
            {"emoji": "😂", "text": "не разрешено"},
            {"emoji": "❤️", "text": "приятно"},
            {"emoji": "❤️", "text": "повтор"},
            {"emoji": "🙏", "text": "спасибо"},
            {"emoji": "🔥", "text": "не разрешено"},
            {"emoji": "😱", "text": "третья реакция"},
        ])
        self.assertEqual(line, "❤️ — приятно; 🙏 — спасибо")
        self.assertEqual(bot.format_ai_reaction_prompt([{"emoji": "😱", "text": "опасность"}], serious=True), "")

    def test_review_keyboard_uses_only_short_callback_payloads(self):
        markup = bot.review_keyboard("rv0123456789abcdefab", version=3)
        callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("review:publish:rv0123456789abcdefab:3", callbacks)
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in callbacks))



    def test_review_preview_and_publish_support_album_media(self):
        uid = "111111111"
        item = {
            "id": "rv-album-1", "version": 1, "status": "pending_review",
            "kind": "manual_tip", "kind_label": "Материал редактора с альбомом",
            "headline": "Закрытие дороги в центре Нячанга",
            "title": "Закрытие дороги в центре Нячанга",
            "body": "Сегодня утром движение перекрыли для ремонта. Водителям стоит использовать соседнюю улицу.",
            "media_items": [
                {"type": "photo", "file_id": "photo-1"},
                {"type": "video", "file_id": "video-1"},
            ],
            "image_file_id": "photo-1", "media_type": "photo",
            "image_url": "", "image_query": "",
            "preview_messages": [], "review_status_messages": [],
            "planned_at": "2026-10-27T09:15:00+07:00",
        }
        def fake_api(method, payload):
            if method == "sendMediaGroup":
                return [
                    {"message_id": 901, "chat": {"id": int(uid)}},
                    {"message_id": 902, "chat": {"id": int(uid)}},
                ]
            raise AssertionError(f"unexpected Telegram method {method}")
        state = {"pending_news_review": item, "review_queue": [], "review_editing": {},
                 "daily_counts": {}, "ads": []}
        with patch.object(bot, "ADMIN_USER_IDS", {uid}), \
             patch.object(bot, "api", side_effect=fake_api) as api_mock, \
             patch.object(bot, "send_dm", return_value={"message_id": 903, "chat": {"id": int(uid)}}):
            self.assertTrue(bot.send_review_preview(item))
            preview_media = json.loads(api_mock.call_args.args[1]["media"])
            self.assertEqual([part["media"] for part in preview_media], ["photo-1", "video-1"])
            self.assertIn("<b>Закрытие дороги в центре Нячанга</b>", preview_media[0]["caption"])

        state["pending_news_review"] = item
        with patch.object(bot, "api", side_effect=fake_api) as api_mock, \
             patch.object(bot, "bump_today"), \
             patch.object(bot, "delete_review_previews"), \
             patch.object(bot, "send_dm"):
            result = bot.publish_review_item(state, item)
        self.assertEqual(result["message_id"], 901)
        self.assertEqual(api_mock.call_args.args[0], "sendMediaGroup")
        self.assertEqual(state["pending_news_review"], {})

    def test_ad_review_keyboard_has_versioned_callbacks(self):
        markup = bot.ad_review_keyboard({"draft_id": "ad123456789abc", "review_version": 4})
        callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("adreview:publish:ad123456789abc:4", callbacks)
        self.assertIn("adreview:reject:ad123456789abc:4", callbacks)
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in callbacks))

    def test_stale_ad_review_button_cannot_publish_current_draft(self):
        uid = 111111111
        state = {
            "pending_ad": {
                "draft_id": "ad123456789abc", "review_version": 4,
                "previewed_at": "2026-10-27T08:00:00+07:00",
                "review_due_at": "2026-10-27T08:15:00+07:00",
                "review_status": "pending_review", "created_by": uid,
            },
            "pending_news_review": {}, "review_queue": [], "review_editing": {},
            "ads": [], "daily_counts": {},
        }
        callback = {
            "from": {"id": uid}, "data": "adreview:publish:ad123456789abc:3",
            "message": {"chat": {"id": uid}, "message_id": 999}
        }
        with patch.object(bot, "ADMIN_USER_IDS", {str(uid)}), \
             patch.object(bot, "send_dm") as dm_mock, \
             patch.object(bot, "publish_ad") as publish_mock:
            bot.handle_callback(callback, state)
        publish_mock.assert_not_called()
        dm_mock.assert_called_once()
        self.assertIn("старому", str(dm_mock.call_args.args[1]))

    def test_pending_ad_auto_publish_requires_confirmed_preview_and_due_time(self):
        uid = "111111111"
        current = datetime(2026, 10, 27, 8, 20, tzinfo=bot.TZ)
        pending = {
            "draft_id": "ad123456789abc", "review_version": 1,
            "previewed_at": "2026-10-27T08:00:00+07:00",
            "review_due_at": "2026-10-27T08:15:00+07:00",
            "review_status": "pending_review", "created_by": uid,
            "text": "A valid advertisement body", "top_hours": 1, "life_hours": 24,
        }
        state = {"pending_ad": pending, "ads": [], "daily_counts": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "publish_ad") as publish_mock:
            bot.process_ad_timers(state)
        publish_mock.assert_called_once_with(pending, state, uid)

        no_preview = {"pending_ad": {"text": "draft", "review_due_at": "2026-10-27T08:10:00+07:00",
                                     "review_status": "pending_review"},
                      "ads": [], "daily_counts": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "publish_ad") as never_publish:
            bot.process_ad_timers(no_preview)
        never_publish.assert_not_called()



    def test_verified_urgent_auto_publish_requires_trusted_recent_source_image_and_serious_signal(self):
        self.assertTrue(bot.is_verified_urgent_source(
            "https://khanhhoa.gov.vn/emergency-flood",
            "Cảnh báo khẩn cấp: ngập lụt, người dân cần sơ tán",
            "Officials issue flood warning and evacuation instructions.",
            "article_metadata",
            "https://cdn.khanhhoa.gov.vn/flood.jpg",
        ))
        self.assertFalse(bot.is_verified_urgent_source(
            "https://random-blog.example/emergency-flood",
            "Cảnh báo khẩn cấp ngập lụt",
            "Evacuation",
            "article_metadata",
            "https://random-blog.example/flood.jpg",
        ))
        self.assertFalse(bot.is_verified_urgent_source(
            "https://khanhhoa.gov.vn/emergency-flood",
            "Cảnh báo khẩn cấp ngập lụt",
            "Evacuation",
            "article_metadata",
            "",
        ))
        self.assertFalse(bot.is_verified_urgent_source(
            "https://khanhhoa.gov.vn/emergency-flood",
            "Cảnh báo khẩn cấp ngập lụt",
            "Evacuation",
            "",
            "https://cdn.khanhhoa.gov.vn/flood.jpg",
        ))
        with patch.object(bot, "URGENT_AUTO_PUBLISH", False):
            self.assertFalse(bot.is_verified_urgent_source(
                "https://khanhhoa.gov.vn/emergency-flood",
                "Cảnh báo khẩn cấp ngập lụt",
                "Evacuation",
                "article_metadata",
                "https://cdn.khanhhoa.gov.vn/flood.jpg",
            ))

    def test_production_weather_refuses_open_meteo_fallback_without_commercial_key(self):
        env = {"GITHUB_ACTIONS": "true", "BOT_TEST_MODE": "", "WEATHERAPI_KEY": ""}
        with patch.dict(os.environ, env), patch.object(bot, "WEATHERAPI_KEY", ""), patch.object(bot, "get_url") as url_mock:
            with self.assertRaisesRegex(RuntimeError, "WEATHERAPI_KEY"):
                bot.fetch_weather_data()
        url_mock.assert_not_called()

    def test_news_sources_are_private_metadata_but_weather_license_credit_remains_public(self):
        news = {
            "kind": "news", "headline": "Road closure in Nha Trang",
            "body": "A current road closure affects residents. Use an alternative route.",
            "link": "https://publisher.example/road-closure",
            "source_url": "https://publisher.example/road-closure",
            "source_name": "publisher.example",
            "source_published_at": "2026-10-27T09:00:00+07:00",
            "source_date_basis": "article_metadata", "score": 90,
            "id": "rv-test", "version": 1,
            "planned_at": "2026-10-27T10:00:00+07:00"
        }
        public_news = bot.review_post_text(news)
        private_status = bot.review_status_text(news)
        self.assertNotIn("publisher.example/road-closure", public_news)
        self.assertIn("publisher.example/road-closure", private_status)
        weather = {
            "kind": "weather_morning", "headline": "Погода в Нячанге",
            "body": "Вероятность дождя сегодня — до 40%.",
            "source_attribution_html": 'Источник прогноза: <a href="https://www.weatherapi.com/">WeatherAPI.com</a>',
        }
        self.assertIn("WeatherAPI.com", bot.review_post_text(weather))



    def test_oversized_review_stops_autopublish_and_keeps_editor_slot_open(self):
        current = datetime(2026, 10, 27, 10, 30, tzinfo=bot.TZ)
        planned = current - timedelta(minutes=1)
        item = {
            "id": "rv-long-caption", "kind": "news", "kind_label": "Полезная новость",
            "headline": "Длительное обновление для жителей Нячанга",
            "title": "Длительное обновление для жителей Нячанга",
            "body": "Проверенная информация для жителей и приезжих в Нячанге. " * 45,
            "status": "pending_review", "planned_at": planned.isoformat(),
            "slot_key": "weather_morning", "preview_messages": [],
            "review_status_messages": [],
        }
        state = {
            "pending_news_review": item, "review_queue": [], "daily_counts": {},
            "published_news": [], "paused": False, "pause_until": "", "ads": [],
        }
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "is_paused", return_value=False), \
             patch.object(bot, "send_dm") as dm_mock, \
             patch.object(bot, "mark_slot") as mark_mock, \
             patch.object(bot, "post_with_image") as post_mock:
            bot.process_news_review(state)
        self.assertEqual(item["status"], "needs_edit")
        self.assertEqual(item["needs_edit_reason"], "caption_too_long")
        self.assertEqual(item["planned_at"], planned.isoformat())
        self.assertEqual(state["pending_news_review"]["id"], "rv-long-caption")
        mark_mock.assert_not_called()
        post_mock.assert_not_called()
        self.assertTrue(dm_mock.called)

    def test_verified_urgent_publish_can_preserve_existing_editor_draft(self):
        current = datetime(2026, 10, 27, 10, 30, tzinfo=bot.TZ)
        active = {
            "id": "rv-editor-active", "kind": "news", "status": "pending_review",
            "headline": "Текущий черновик", "title": "Текущий черновик",
            "body": "Проверенный текст для отдельной редакторской публикации.",
            "planned_at": (current + timedelta(minutes=15)).isoformat(),
        }
        urgent = {
            "id": "rv-urgent", "kind": "news", "status": "pending_review", "urgent": True,
            "headline": "Официальное предупреждение о наводнении",
            "title": "Официальное предупреждение о наводнении",
            "source_title": "Cảnh báo ngập lụt",
            "body": "Местные службы предупредили об угрозе наводнения. Следуйте официальным указаниям и избегайте затопленных дорог.",
            "link": "https://khanhhoa.gov.vn/emergency-flood",
            "source_url": "https://khanhhoa.gov.vn/emergency-flood",
            "source_published_at": current.isoformat(),
            "image_url": "https://cdn.khanhhoa.gov.vn/flood.jpg",
            "image_query": "Nha Trang Vietnam flood warning",
            "idempotency_key": "https://khanhhoa.gov.vn/emergency-flood",
            "preview_messages": [],
        }
        queued = {"id": "rv-queued", "kind": "news", "status": "pending_review", "link": "https://news.example/queued"}
        state = {
            "pending_news_review": active, "review_queue": [queued], "daily_counts": {},
            "published_news": [], "seen_links": [], "seen_titles": [],
            "seen_fingerprints": [], "ads": [],
        }
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "post_with_image", return_value={"message_id": 987, "chat": {"id": -1001}}), \
             patch.object(bot, "send_dm"), \
             patch.object(bot, "delete_review_previews") as delete_mock:
            result = bot.publish_review_item(state, urgent, preserve_pending=True)
        self.assertEqual(result["message_id"], 987)
        self.assertEqual(state["pending_news_review"], active)
        self.assertEqual(state["review_queue"], [queued])
        self.assertEqual(active["status"], "pending_review")
        self.assertEqual(urgent["status"], "published")
        delete_mock.assert_not_called()



    def test_failed_news_preview_is_retried_before_any_timer_can_publish(self):
        current = datetime(2026, 10, 27, 10, 30, tzinfo=bot.TZ)
        item = {
            "id": "rv-needs-preview", "kind": "news", "status": "needs_preview",
            "headline": "Изменение движения в Нячанге",
            "title": "Изменение движения в Нячанге",
            "body": "Временное изменение движения затронет жителей города. Проверьте маршрут перед выездом.",
            "image_url": "https://cdn.publisher.example/road.jpg",
            "next_preview_retry_at": (current - timedelta(minutes=1)).isoformat(),
            "planned_at": (current + timedelta(minutes=15)).isoformat(),
            "preview_messages": [], "review_status_messages": [],
        }
        state = {"pending_news_review": item, "review_queue": [], "daily_counts": {}, "paused": False}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "send_review_preview", return_value=True) as preview_mock, \
             patch.object(bot, "send_dm"):
            bot.process_news_review(state)
        preview_mock.assert_called_once_with(item)
        self.assertEqual(item["status"], "pending_review")
        self.assertEqual(item["planned_at"], (current + timedelta(minutes=15)).isoformat())
        self.assertNotIn("next_preview_retry_at", item)

    def test_ad_draft_gets_private_preview_and_fifteen_minute_timer_automatically(self):
        current = datetime(2026, 10, 27, 10, 30, tzinfo=bot.TZ)
        uid = "111111111"
        draft = {
            "format": "1/24", "top_hours": 1, "life_hours": 24,
            "created_by": uid, "awaiting_content": False, "text": "A useful local service for Nha Trang residents."
        }
        state = {"pending_ad": draft, "ads": [], "daily_counts": {}}
        sent = []
        def fake_send(user_id, text, markup=None):
            sent.append((user_id, text, markup))
            return {"message_id": 100 + len(sent), "chat": {"id": user_id}}
        with patch.object(bot, "now", return_value=current), patch.object(bot, "send_dm", side_effect=fake_send):
            result = bot.show_ad_preview(state, uid)
        self.assertTrue(result)
        self.assertEqual(draft["review_status"], "pending_review")
        self.assertEqual(draft["previewed_at"], current.isoformat())
        self.assertEqual(draft["review_due_at"], (current + timedelta(minutes=15)).isoformat())
        self.assertEqual(draft["review_version"], 1)
        self.assertEqual(sent[-1][2]["inline_keyboard"][0][0]["callback_data"],
                         f'adreview:publish:{draft["draft_id"]}:1')

    def test_ad_preview_delivery_failure_does_not_start_publication_timer(self):
        current = datetime(2026, 10, 27, 10, 30, tzinfo=bot.TZ)
        uid = "111111111"
        draft = {
            "format": "1/24", "top_hours": 1, "life_hours": 24,
            "created_by": uid, "awaiting_content": False, "text": "A useful local service for Nha Trang residents."
        }
        state = {"pending_ad": draft, "ads": [], "daily_counts": {}}
        with patch.object(bot, "now", return_value=current), \
             patch.object(bot, "send_dm", return_value=None):
            result = bot.show_ad_preview(state, uid)
        self.assertFalse(result)
        self.assertNotIn("previewed_at", draft)
        self.assertNotIn("review_due_at", draft)
        self.assertNotIn("review_status", draft)


if __name__ == "__main__":
    unittest.main()
