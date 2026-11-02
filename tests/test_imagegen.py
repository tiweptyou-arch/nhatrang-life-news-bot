import base64
import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch

import imagegen


class ImageProviderTests(unittest.TestCase):
    def test_generate_uses_documented_endpoint_and_returns_image_and_cost(self):
        image = b"sample-image-bytes"
        body = {
            "data": [{"b64_json": base64.b64encode(image).decode("ascii")}],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"text_tokens": 100, "image_tokens": 0},
                "output_tokens": 200,
                "output_tokens_details": {"image_tokens": 200},
            },
        }

        class Response(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with patch.object(imagegen.urllib.request, "urlopen", return_value=Response(json.dumps(body).encode())) as open_url:
            result, cost = imagegen.generate("Nha Trang coastline", api_key="unit-test-key")

        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/images/generations")
        self.assertEqual(request.get_header("Authorization"), "Bearer unit-test-key")
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["model"], "gpt-image-2.5-flare")
        self.assertEqual(payload["size"], "1536x864")
        self.assertEqual(payload["quality"], "low")
        self.assertEqual(result, image)
        self.assertAlmostEqual(cost, 0.0065)

    def test_generate_requires_real_secret(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                imagegen.generate("test")

    def test_generated_prompt_never_requests_text_in_image(self):
        prompt = imagegen.prompt_for({"kind": "weather_morning", "weather_image_category": "rain"})
        self.assertIn("no readable text", prompt)
        self.assertIn("Nha Trang", prompt)


if __name__ == "__main__":
    unittest.main()
