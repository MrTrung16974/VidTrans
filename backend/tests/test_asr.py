import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.asr import ASRConfig, ASRService


def _segment(start, end, text):
    word = SimpleNamespace(word=text, start=start, end=end, probability=0.9)
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=-0.2, no_speech_prob=0.1, words=[word])


class FakeFasterModel:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def transcribe(self, path, **options):
        self.calls.append(options)
        return iter(self.results.pop(0)), SimpleNamespace(duration=10.0)


def service(model, **config):
    with patch("pipeline.asr.faster_whisper_available", return_value=True):
        asr = ASRService(ASRConfig(engine="faster", **config))
    asr.engine = lambda name: model
    return asr


class ASRServiceTests(unittest.TestCase):
    def test_faster_segments_match_whisper_shape_and_report_progress(self):
        model = FakeFasterModel([[_segment(0.0, 4.0, "你好"), _segment(4.0, 10.0, "谢谢")]])
        progress = []
        segments = service(model).transcribe_chinese("small", Path("v.mp4"), progress=lambda p, t: progress.append((p, t)))
        self.assertEqual([s["text"] for s in segments], ["你好", "谢谢"])
        self.assertEqual(segments[0]["words"][0], {"word": "你好", "start": 0.0, "end": 4.0, "probability": 0.9})
        self.assertEqual(progress, [(4.0, 10.0), (10.0, 10.0)])
        options = model.calls[0]
        self.assertEqual((options["language"], options["beam_size"], options["vad_filter"]), ("zh", 5, True))
        self.assertTrue(options["word_timestamps"])

    def test_retries_without_vad_when_first_pass_is_empty(self):
        model = FakeFasterModel([[], [_segment(1.0, 2.0, "你好")]])
        segments = service(model).transcribe_chinese("small", Path("v.mp4"))
        self.assertEqual(len(segments), 1)
        self.assertFalse(model.calls[1]["vad_filter"])
        self.assertEqual(model.calls[1]["beam_size"], 1)

    def test_beam_and_vad_are_configurable(self):
        model = FakeFasterModel([[_segment(0.0, 1.0, "好")]])
        service(model, beam_size=2, vad=False).transcribe_chinese("small", Path("v.mp4"))
        self.assertEqual((model.calls[0]["beam_size"], model.calls[0]["vad_filter"]), (2, False))

    def test_falls_back_to_openai_when_faster_whisper_missing(self):
        with patch("pipeline.asr.faster_whisper_available", return_value=False):
            self.assertEqual(ASRService(ASRConfig(engine="faster")).engine_name, "openai")

    def test_env_validation(self):
        with patch.dict("os.environ", {"VIDTRANS_ASR_ENGINE": "gpu"}):
            with self.assertRaises(ValueError):
                ASRConfig.from_env()
        with patch.dict("os.environ", {"VIDTRANS_ASR_BEAM_SIZE": "1", "VIDTRANS_ASR_VAD": "0"}):
            config = ASRConfig.from_env()
        self.assertEqual((config.beam_size, config.vad), (1, False))
