# Traceability — Tối ưu khớp giọng tự động

| Requirement | Code | Test |
|---|---|---|
| BR01_01 / AC01_01 | `pipeline/voice_routing.py::analyze_pitch` | `test_identifies_low_and_high_pitch_voice_styles` |
| BR01_02 / AC01_02 | `route_segments_by_pitch` neighbor resolution | `test_silence_between_matching_voices_uses_neighbors` |
| BR01_03 / AC01_02-03 | isolated flip smoothing | `test_weak_isolated_flip_is_smoothed_but_strong_switch_is_kept` |
| BR01_04 | global/fallback resolution | `test_all_unvoiced_segments_use_selected_fallback` |
| BR01_05 / AC01_04 | `voice_routing` metadata | routing tests |
| BR01_06 / AC01_05 | `tts_timing.available_voice_window` | `test_available_window_borrows_real_pause_without_consuming_all_of_it` |
| BR01_07 / AC01_06 | `bounded_tempo`, TTS fit metadata | tempo/window tests |
| BR01_08-10 / AC01_07 | `VOICE_PRESETS`, `ProcessingRequest.voice_style`, TTS metadata, UI select | language/domain tests + JS syntax |
