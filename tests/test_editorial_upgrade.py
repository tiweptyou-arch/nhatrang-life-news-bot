import json
import os
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'test-token-for-local-unit-tests')
import bot


class EditorialUpgradeTests(unittest.TestCase):
    def test_risk_gate_does_not_trust_model_score_for_sensitive_news(self):
        official = 'https://example.gov.vn/news'
        self.assertEqual(bot.editorial_verification_level(
            {'category': 'city', 'score_parts': {'source_confidence': 4}}, official), 'GREEN')
        self.assertEqual(bot.editorial_verification_level(
            {'category': 'health', 'score_parts': {'source_confidence': 5}}, official), 'YELLOW')
        self.assertEqual(bot.editorial_verification_level(
            {'category': 'city', 'headline': 'Слух о преступлении', 'score_parts': {'source_confidence': 5}}, official), 'RED')

    def test_auto_publish_channel_write_requires_explicit_internal_marker(self):
        with patch.object(bot, 'AUTO_PUBLISH', True):
            with self.assertRaises(RuntimeError):
                bot._assert_editor_private_target('sendPhoto', {'chat_id': bot.CHANNEL})
            bot._assert_editor_private_target('sendPhoto', {
                'chat_id': bot.CHANNEL, '_auto_publish_news': True})
            with self.assertRaises(RuntimeError):
                bot._assert_editor_private_target('sendMessage', {
                    'chat_id': bot.CHANNEL, '_auto_publish_news': True})

    def test_openai_response_api_usage_is_logged_and_costed(self):
        output = {
            'decision': 'draft', 'local_relevance': 4, 'practical_usefulness': 4,
            'source_confidence': 4, 'freshness_confidence': 4, 'novelty': 4,
            'headline_ru': 'В городе изменили расписание автобусов',
            'audience_question': 'Этим маршрутом пользуетесь?',
            'body_ru': 'Власти города сообщили об изменении расписания автобусов. Новый график начнёт действовать на следующей неделе. Пассажирам следует проверить маршрут перед поездкой.',
            'category': 'city', 'why_it_matters': 'Это помогает планировать поездки.',
            'image_query_en': 'Nha Trang city bus route', 'reaction_prompt': [],
            'uncertainties': [], 'verification_notes': 'Официальное сообщение'
        }
        response_body = json.dumps({
            'output_text': json.dumps(output, ensure_ascii=False),
            'usage': {'input_tokens': 500, 'output_tokens': 300}
        }).encode()

        class Response(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        state = {'image_usage': {}}
        with patch.object(bot, 'OPENAI_API_KEY', 'unit-test-key'), \
             patch.object(bot, 'now', return_value=datetime(2026, 11, 5, 12, 0, tzinfo=bot.TZ)), \
             patch.object(bot.urllib.request, 'urlopen', return_value=Response(response_body)) as open_url:
            result = bot.ai_editorial_review(
                state, 'Bus schedule change', 'The city announced bus schedule changes starting next week.',
                'https://example.gov.vn/news', bot.now())

        request = open_url.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(request.get_header('Authorization'), 'Bearer unit-test-key')
        self.assertEqual(payload['model'], 'gpt-6-luna')
        self.assertEqual(result['category'], 'city')
        self.assertAlmostEqual(state['openai_usage']['monthly_usd'], 0.0002)
        self.assertEqual(state['openai_usage']['ledger'][0]['input_tokens'], 500)


    def test_public_news_template_contains_why_question_and_clickable_footer(self):
        text = bot.review_post_text({
            'kind': 'news', 'category': 'city', 'headline': 'Изменение в городе',
            'body': 'Короткий редакционный текст с проверяемыми фактами.',
            'why_it_matters': 'Это повлияет на ежедневные поездки.',
            'audience_question': 'Как считаете, это хорошее изменение?',
            'reaction_line': '👍 — поддерживаю'
        })
        self.assertIn('Почему важно:', text)
        self.assertIn('Как считаете, это хорошее изменение?', text)
        self.assertIn('href="https://t.me/NhaTrangLifeNews"', text)
        self.assertIn('href="https://t.me/fsfinesed"', text)

    def test_weather_forecast_includes_uv_and_rain_gear_guidance(self):
        now = datetime(2026, 11, 5, 6, 0, tzinfo=bot.TZ)
        times = [datetime(2026, 11, 5, h, 0, tzinfo=bot.TZ).isoformat() for h in (7, 9, 12, 15, 18, 21)]
        data = {
            'retrieved_at': now.isoformat(),
            'current': {'temperature_2m': 28, 'apparent_temperature': 31, 'weather_code': 2},
            'hourly': {'time': times, 'temperature_2m': [28] * 6,
                       'precipitation_probability': [80] * 6, 'weather_code': [2] * 6,
                       'wind_speed_10m': [12] * 6, 'uv_index': [7] * 6},
            'daily': {'time': ['2026-11-05'], 'temperature_2m_min': [25],
                      'temperature_2m_max': [31], 'precipitation_probability_max': [80],
                      'wind_gusts_10m_max': [20], 'uv_index_max': [7]}
        }
        _headline, body, _date = bot.format_weather_forecast(data, 'morning', now)
        self.assertIn('UV до 7', body)
        self.assertIn('SPF 30+', body)
        self.assertIn('дождевик', body)



    def test_missing_openai_key_uses_free_local_illustration(self):
        class LocalCard:
            def save(self, output, **_kwargs):
                output.write(b'free-local-jpeg')

        state = {'generated_image_cache': {}}
        item = {'kind': 'news', 'headline': 'Изменение в городе', 'category': 'city',
                'image_query': 'Nha Trang city life'}
        with patch.object(bot, 'OPENAI_API_KEY', ''), \
             patch.object(bot, 'fallback_editorial_png', return_value=b'local-png'), \
             patch.object(bot.imagegen, 'render_card', return_value=LocalCard()), \
             patch.object(bot.imagegen, 'generate') as paid_generate:
            self.assertTrue(bot.prepare_generated_image(state, item))
        self.assertEqual(item['image_source'], 'free_local_illustration')
        self.assertEqual(item['image_generation_cost_usd'], 0.0)
        paid_generate.assert_not_called()

    def test_image_generation_stops_when_daily_budget_is_exhausted(self):
        today = bot.now().date().isoformat()
        state = {'image_usage': {'day': today, 'daily_usd': 0.25, 'daily_count': 1}}
        item = {'kind': 'news', 'headline': 'Изменение в городе', 'category': 'city'}
        with patch.object(bot, 'OPENAI_API_KEY', 'unit-test-key'), \
             patch.object(bot.imagegen, 'generate') as generate:
            result = bot.prepare_generated_image(state, item)
        self.assertFalse(result)
        self.assertEqual(item['image_hold_reason'], 'daily_image_budget_reached')
        generate.assert_not_called()

    def test_source_configuration_is_external_and_has_vietnamese_and_english_feeds(self):
        feeds = bot.load_news_source_config()['feeds']
        self.assertGreaterEqual(len(feeds), 8)
        self.assertIn('vi', {entry['language'] for entry in feeds})
        self.assertIn('en', {entry['language'] for entry in feeds})

    def test_openai_monthly_budget_blocks_request_before_network_call(self):
        state = {
            'image_usage': {'month': bot.now().strftime('%Y-%m'), 'monthly_usd': 7.99},
            'openai_usage': {'month': bot.now().strftime('%Y-%m'), 'monthly_usd': 0.01}
        }
        with patch.object(bot, 'OPENAI_API_KEY', 'unit-test-key'), \
             patch.object(bot.urllib.request, 'urlopen') as open_url:
            result = bot.ai_editorial_review(state, 'Test', 'A sufficiently long source text. ' * 20,
                                             'https://example.gov.vn/news', bot.now())
        self.assertIsNone(result)
        open_url.assert_not_called()


if __name__ == '__main__':
    unittest.main()
