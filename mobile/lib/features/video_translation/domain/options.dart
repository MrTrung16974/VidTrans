/// Choice lists from the web form (web/index.html, `#batchForm`).
typedef Option = ({String value, String label});

const modeOptions = <Option>[
  (value: '1', label: 'Chỉ phụ đề — giữ âm thanh gốc'),
  (value: '2', label: 'Phụ đề + lồng tiếng'),
  (value: '3', label: 'Phụ đề + lồng tiếng + nhạc nền'),
];

const modeHints = {
  1: 'Giữ nguyên âm thanh gốc, chỉ dịch và chèn phụ đề theo ngôn ngữ đầu ra. Nhanh nhất và không chạy TTS.',
  2: 'Tạo giọng đọc theo ngôn ngữ đầu ra, đồng bộ từng câu và có thể giữ tiếng gốc ở mức nhỏ.',
  3: 'Giống chế độ lồng tiếng, đồng thời lặp nhạc nền vừa đủ thời lượng và tự cân bằng âm lượng.',
};

const sourceLanguageOptions = <Option>[
  (value: 'auto', label: 'Tự nhận diện'),
  (value: 'zh-CN', label: 'Tiếng Trung'),
  (value: 'vi', label: 'Tiếng Việt'),
  (value: 'en', label: 'Tiếng Anh'),
  (value: 'ja', label: 'Tiếng Nhật'),
  (value: 'ko', label: 'Tiếng Hàn'),
  (value: 'th', label: 'Tiếng Thái'),
  (value: 'id', label: 'Tiếng Indonesia'),
  (value: 'es', label: 'Tiếng Tây Ban Nha'),
];

const targetLanguageOptions = <Option>[
  (value: 'vi', label: 'Tiếng Việt'),
  (value: 'en', label: 'Tiếng Anh'),
  (value: 'zh-CN', label: 'Tiếng Trung'),
  (value: 'ja', label: 'Tiếng Nhật'),
  (value: 'ko', label: 'Tiếng Hàn'),
  (value: 'th', label: 'Tiếng Thái'),
  (value: 'id', label: 'Tiếng Indonesia'),
  (value: 'es', label: 'Tiếng Tây Ban Nha'),
];

const subtitleSourceOptions = <Option>[
  (value: 'speech', label: 'Chỉ nghe lời thoại (Whisper) — nhanh'),
  (value: 'auto', label: 'Tự động — OCR + Whisper (chậm hơn)'),
  (value: 'independent', label: 'OCR + ASR độc lập — video đa ngôn ngữ'),
  (value: 'burned', label: 'Chữ có sẵn trên video'),
];

const whisperModelOptions = <Option>[
  (value: 'base', label: 'Base — nhanh'),
  (value: 'small', label: 'Small — cân bằng'),
  (value: 'medium', label: 'Medium — chính xác'),
];

const voiceModeOptions = <Option>[
  (value: 'auto', label: 'Tự động theo cao độ và lời thoại'),
  (value: 'manual', label: 'Thủ công'),
];

const voiceStyleOptions = <Option>[
  (value: 'auto', label: 'Tự động theo nội dung — đề xuất'),
  (value: 'natural', label: 'Tự nhiên — cân bằng'),
  (value: 'warm', label: 'Ấm áp — gần gũi'),
  (value: 'bright', label: 'Tươi sáng — rõ ràng'),
  (value: 'deep', label: 'Trầm — chắc giọng'),
  (value: 'energetic', label: 'Năng động — nhanh và sáng'),
  (value: 'gentle', label: 'Nhẹ nhàng — tâm sự, chữa lành'),
  (value: 'storyteller', label: 'Kể chuyện — chậm và cuốn hút'),
  (value: 'news', label: 'Bản tin — rõ, dứt khoát'),
  (value: 'commercial', label: 'Quảng cáo — nổi bật, thuyết phục'),
  (value: 'youthful', label: 'Trẻ trung — sáng và thân thiện'),
  (value: 'authoritative', label: 'Uy tín — trầm và chuyên nghiệp'),
  (value: 'calm', label: 'Điềm tĩnh — chậm, dễ nghe'),
  (value: 'cheerful', label: 'Vui vẻ — nhanh và tích cực'),
];

const placementOptions = <Option>[
  (value: 'replace_original', label: 'Thay đúng vị trí chữ nguồn'),
  (value: 'above_original', label: 'Đặt ngay trên chữ nguồn'),
  (value: 'bottom_safe', label: 'Vùng an toàn phía dưới'),
];

const backgroundStyleOptions = <Option>[
  (value: 'box', label: 'Hộp nền ôm theo phụ đề'),
  (value: 'blurred_bottom', label: 'Làm mờ vừa vùng chữ nguồn'),
  (value: 'none', label: 'Không dùng nền'),
];

/// Order of the web `<select>`s; the server status decides which are usable.
const translationProviderOrder = ['google', 'gemini', 'qwen3', 'translategemma'];
const ttsProviderOrder = ['edge', 'azure', 'elevenlabs', 'cit'];

/// Providers the server can synthesize a voice sample for.
const voicePreviewProviders = {'edge', 'elevenlabs', 'cit'};

/// Pulls social video links from pasted text. The server does the
/// authoritative extraction; this only counts and lists them for the UI.
List<String> extractLinks(String text) {
  final pattern = RegExp(r'https?://[^\s<>"]+', caseSensitive: false);
  final seen = <String>{};
  return [
    for (final match in pattern.allMatches(text))
      if (seen.add(match.group(0)!)) match.group(0)!,
  ];
}
