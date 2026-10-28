import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline.asr import ASRConfig, ASRService, bound_feature_memory, resolve_cpu_threads


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
            # The recovery clip starts up to 10s before the gap, here at 0s.
            [_segment(10.0, 11.0, "有鬼"), _segment(0.0, 0.4, "好")],
            [],
        ])
        model.feature_extractor = SimpleNamespace(sampling_rate=16000)
        asr = service(model)
        asr._load_audio = lambda model, path: np.zeros(16000 * 40, dtype=np.float32)
        segments = asr.transcribe_chinese("small", Path("v.mp4"))
        self.assertEqual([s["text"] for s in segments], ["你好", "有鬼", "谢谢"])
        self.assertAlmostEqual(segments[1]["start"], 10.0)
        self.assertAlmostEqual(segments[1]["words"][0]["end"], 11.0)
        self.assertFalse(model.calls[1]["vad_filter"])
        self.assertFalse(model.calls[1]["condition_on_previous_text"])
        self.assertNotIn("hallucination_silence_threshold", model.calls[1])
        self.assertEqual(len(model.inputs[1]), int(16000 * 30.3))
        # The 8s tail after the last segment is a gap too.
        self.assertEqual(len(model.calls), 3)

    def test_muted_music_is_silenced_and_not_heard_again(self):
        import numpy as np

        model = FakeFasterModel([[_segment(0.0, 2.0, "你好"), _segment(30.0, 32.0, "谢谢"), _segment(33.0, 40.0, "谢谢你们大家好今天我们一起来")]])
        model.feature_extractor = SimpleNamespace(sampling_rate=16000)
        asr = service(model)
        asr._load_audio = lambda model, path: np.ones(16000 * 40, dtype=np.float32)
        asr.transcribe("small", Path("v.mp4"), source_language="zh-CN", muted=[(3.0, 29.0)])
        heard = model.inputs[0]
        self.assertEqual(float(heard[16000 * 10]), 0.0)
        self.assertEqual(float(heard[16000 * 1]), 1.0)
        # Only 2s of the 28s gap are unmuted, so no recovery pass runs.
        self.assertEqual(len(model.calls), 1)

    def test_repetition_loop_is_dropped_and_its_time_heard_again(self):
        import numpy as np

        # Job 05a05e62: "我死定了" looped, one segment stretched over 37s of real dialogue.
        loop = [_segment(0.0, 2.0, "你好"), _segment(190.07, 195.21, "我死定了"),
                _segment(195.21, 232.56, "我死定了"), _segment(232.56, 233.1, "我死定了"),
                _segment(240.0, 241.0, "谢谢")]
        model = FakeFasterModel([loop, [_segment(5.0, 6.0, "我凭什么后悔"), _segment(30.0, 31.0, "我想吃水果")], [], []])
        model.feature_extractor = SimpleNamespace(sampling_rate=16000)
        asr = service(model)
        asr._load_audio = lambda model, path: np.zeros(16000 * 242, dtype=np.float32)
        texts = [s["text"] for s in asr.transcribe_chinese("small", Path("v.mp4"))]
        self.assertEqual(texts.count("我死定了"), 1)
        self.assertIn("我想吃水果", texts)

    def test_dropped_loop_lines_are_kept_aside_for_captions_to_confirm(self):
        import numpy as np

        model = FakeFasterModel([[_segment(float(i), float(i) + 0.9, "祝你幸福") for i in range(5)]])
        model.feature_extractor = SimpleNamespace(sampling_rate=16000)
        asr = service(model)
        asr._load_audio = lambda model, path: np.zeros(16000 * 5, dtype=np.float32)
        result = asr.transcribe("small", Path("v.mp4"), source_language="zh-CN")
        self.assertEqual(len(result.segments) + len(result.repeated), 5)
        self.assertGreaterEqual(len(result.repeated), 1)

    def test_loop_filter_keeps_real_repeats(self):
        from pipeline.asr import drop_repetition_loops

        def seg(start, end, text):
            return {"start": start, "end": end, "text": text}

        kept = drop_repetition_loops([
            seg(0, 1, "别别别别"), seg(1, 2, "有鬼"), seg(2, 3, "有鬼"),
            seg(3, 9, "有诗,有诗,有诗,有诗,有诗,有诗"), seg(10, 11, "媽媽"), seg(11, 12, "媽媽"), seg(12, 13, "媽媽"),
            seg(14, 15, "喊喊喊喊喊喊"), seg(20, 40, "我死定了"),
        ])
        self.assertEqual([s["text"] for s in kept], ["别别别别", "有鬼", "有鬼", "媽媽", "媽媽"])

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


@unittest.skipUnless(importlib.util.find_spec("faster_whisper"), "faster-whisper required")
class ChunkedFeatureTests(unittest.TestCase):
    def test_chunked_log_mel_matches_whole_file_features(self):
        # Whole-file features of a 65-minute video needed ~3 GB of STFT scratch and got
        # the worker OOM-killed; chunking must not change what the model hears.
        import numpy as np
        from faster_whisper.feature_extractor import FeatureExtractor

        rng = np.random.default_rng(7)
        reference = FeatureExtractor()
        model = SimpleNamespace(feature_extractor=FeatureExtractor())
        bound_feature_memory(model)
        with patch("pipeline.asr.MEL_CHUNK_FRAMES", 700):
            for samples in (300, 16000 * 5, 700 * 160 - 160, 700 * 160 + 7, 16000 * 23 + 321):
                waveform = (rng.standard_normal(samples) * 0.1).astype(np.float32)
                expected = reference(waveform, chunk_length=30)
                actual = model.feature_extractor(waveform, chunk_length=30)
                self.assertEqual(actual.shape, expected.shape)
                np.testing.assert_array_equal(actual, expected)

