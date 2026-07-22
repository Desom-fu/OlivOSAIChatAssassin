"""视觉识别提示词和回复纠偏回归测试。"""

import unittest
from unittest import mock

from OlivOSAIChatAssassin import msg, tools, webTools


class VisionPromptTests(unittest.TestCase):
    def test_ocr_request_keeps_url_and_adds_text_instruction(self):
        captured = {}

        class FakeResponse:
            status_code = 200
            text = ''

            def json(self):
                return {
                    'choices': [{'message': {'content': '{"content":"画面描述","intent":"让用户选择","type":"截图"}'}}],
                    'usage': {},
                }

        def fake_post(url, headers=None, json=None, timeout=None):
            captured['payload'] = json
            return FakeResponse()

        with mock.patch.object(webTools.requests, 'post', side_effect=fake_post):
            result = webTools.call_ai_ocr(
                {
                    'ocr_api': {
                        'api_key': 'key',
                        'api_base': 'https://vision.example/v1',
                        'model': 'vision-model',
                    },
                },
                prompt='content最多160字，intent最多32字，type最多32字',
                image_url='https://example.com/image.jpg',
            )

        self.assertIn('画面描述', result)
        user_content = captured['payload']['messages'][1]['content']
        self.assertEqual(user_content[0]['type'], 'text')
        self.assertEqual(user_content[1]['type'], 'image_url')
        self.assertEqual(user_content[1]['image_url']['url'], 'https://example.com/image.jpg')

    def test_reply_denial_is_repaired_only_for_latest_image(self):
        image_history = [{'message': '[图片：三种服装选项；意图：让用户选择；类型：截图]'}]
        reply = ['小芙还没升级识图功能，暂时看不到图里的具体内容呢~']
        self.assertEqual(msg.repair_vision_denial(reply, image_history), ['我看到了：三种服装选项。'])

        old_image_history = [
            {'message': '[图片：旧图片；意图：普通；类型：照片]'},
            {'message': '现在没有图片'},
        ]
        self.assertEqual(msg.repair_vision_denial(reply, old_image_history), reply)

    def test_image_tag_is_short_but_cache_description_can_be_long(self):
        long_content = '可见文字和选项' * 20
        tag = tools.imgcode_format({
            'content': long_content,
            'intent': '让用户选择',
            'type': '截图',
        })

        tag_content = tag.split('；意图：', 1)[0][len('[图片：'):]
        self.assertEqual(len(tag_content), 32)
        self.assertEqual(tag_content, long_content[:32])


if __name__ == '__main__':
    unittest.main()
