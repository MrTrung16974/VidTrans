# Traceability

| Yêu cầu | Design/code | Unit test | TC | Trạng thái |
|---|---|---|---|---|
| BR01–BR06 | `domain/languages.py`, `domain/models.py`, API/UI | `test_languages.py` | TC_001–005 | PASS |
| BR07–BR08 | `pipeline/asr.py` | `test_asr.py` | TC_006–007 | PASS |
| BR09–BR12 | `infrastructure/translator.py`, `vietnamese_translator.py`, `pipeline/translation.py` | translation tests | TC_008–010 | PASS |
| BR13 | Dockerfile, language font profile | integration | TC_015 | PASS |
| BR14–BR16 | language profile, `main.py` TTS | `test_languages.py` | TC_011–012 | PASS |
| BR17–BR18 | defaults in model/API/resume | `test_languages.py`, OpenAPI check | TC_005,013 | PASS |
