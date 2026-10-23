import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline.asr import ASRConfig, ASRService, resolve_cpu_threads


def _segment(start, end, text):
    word = SimpleNamespace(word=text, start=start, end=end, probability=0.9)
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=-0.2, no_speech_prob=0.1, words=[word])


class FakeFasterModel:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.inputs = []

    def transcribe(self, audio, **options):
        self.calls.append(options)
        self.inputs.append(audio)
        return iter(self.results.pop(0)), SimpleNamespace(
            duration=10.0, language="ja", language_probability=0.93
        )


def service(model, **config):
    with patch("pipeline.asr.faster_whisper_available", return_value=True):
        asr = ASRService(ASRConfig(engine="faster", **config))
    asr.engine = lambda name: model
    asr.decoded = []
    asr._load_audio = lambda model, path: asr.decoded.append(path) or ("pcm", str(path))
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

    def test_long_gap_left_by_vad_is_transcribed_again_without_vad(self):
        import numpy as np

        model = FakeFasterModel([
            [_segment(0.0, 2.0, "你好"), _segment(30.0, 32.0, "谢谢")],
            # The gap clip starts 0.3s before the first segment ends.
            [_segment(10.0, 11.0, "有鬼"), _segment(0.0, 0.4, "好")],
            [],
        ])
        model.feature_extractor = SimpleNamespace(sampling_rate=16000)
        asr = service(model)
        asr._load_audio = lambda model, path: np.zeros(16000 * 40, dtype=np.float32)
        segments = asr.transcribe_chinese("small", Path("v.mp4"))
        self.assertEqual([s["text"] for s in segments], ["你好", "有鬼", "谢谢"])
        self.assertAlmostEqual(segments[1]["start"], 11.7)
        self.assertAlmostEqual(segments[1]["words"][0]["end"], 12.7)
        self.assertFalse(model.calls[1]["vad_filter"])
        self.assertEqual(len(model.inputs[1]), int(16000 * (30.3 - 1.7)))
        # The 8s tail after the last segment is a gap too.
        self.assertEqual(len(model.calls), 3)

    def test_gap_hallucinations_are_discarded(self):
        import numpy as np

        noise = _segment(1.0, 3.0, "谢谢观看")
        noise.avg_logprob, noise.no_speech_prob = -1.4, 0.9
        model = FakeFasterModel([[_segment(0.0, 1.0, "你好")], [noise]])
        model.feature_extractor = SimpleNamespace(sampling_rate=16000)
        asr = service(model)
        asr._load_audio = lambda model, path: np.zeros(16000 * 10, dtype=np.float32)
        segments = asr.transcribe_chinese("small", Path("v.mp4"))
        self.assertEqual([s["text"] for s in segments], ["你好"])

    def test_audio_is_decoded_once_and_reused_by_every_retry(self):
        model = FakeFasterModel([[], [], [_segment(1.0, 2.0, "你好")]])
        asr = service(model)
        asr.transcribe_chinese("small", Path("v.mp4"))
        self.assertEqual(asr.decoded, [Path("v.mp4")])
        self.assertEqual(model.inputs, [("pcm", "v.mp4")] * 3)

    def test_faster_decode_matches_the_engine_internal_decode(self):
        model = SimpleNamespace(feature_extractor=SimpleNamespace(sampling_rate=16000))
        with patch("pipeline.asr.faster_whisper_available", return_value=True):
            asr = ASRService(ASRConfig(engine="faster"))
        decode = Mock(return_value="pcm")
        audio_module = SimpleNamespace(decode_audio=decode)
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(audio=audio_module),
                                        "faster_whisper.audio": audio_module}):
            self.assertEqual(asr._load_audio(model, Path("v.mp4")), "pcm")
        decode.assert_called_once_with("v.mp4", sampling_rate=16000)

    def test_cpu_threads_split_cores_between_concurrent_asr_jobs(self):
        with patch("pipeline.asr.available_cpu_count", return_value=12):
            self.assertEqual(resolve_cpu_threads(0, 1), 12)
            self.assertEqual(resolve_cpu_threads(0, 2), 6)
            self.assertEqual(resolve_cpu_threads(0, 24), 1)
            self.assertEqual(resolve_cpu_threads(3, 2), 3)
        with self.assertRaises(ValueError):
            resolve_cpu_threads(-1, 1)

    def test_beam_and_vad_are_configurable(self):
        model = FakeFasterModel([[_segment(0.0, 1.0, "好")]])
        service(model, beam_size=2, vad=False).transcribe_chinese("small", Path("v.mp4"))
        self.assertEqual((model.calls[0]["beam_size"], model.calls[0]["vad_filter"]), (2, False))

    def test_auto_language_is_not_forced_and_returns_detection(self):
        model = FakeFasterModel([[_segment(0.0, 1.0, "こんにちは")]])
        result = service(model).transcribe("small", Path("v.mp4"), source_language="auto")
        self.assertIsNone(model.calls[0]["language"])
        self.assertEqual((result.detected_language, result.language_probability), ("ja", 0.93))

    def test_explicit_language_uses_catalog_whisper_code(self):
        model = FakeFasterModel([[_segment(0.0, 1.0, "こんにちは")]])
        service(model).transcribe("small", Path("v.mp4"), source_language="ja")
        self.assertEqual(model.calls[0]["language"], "ja")

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
