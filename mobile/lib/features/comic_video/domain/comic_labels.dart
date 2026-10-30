// Vietnamese labels shared with the web (web/js/comic.js, animation-editor.js).

const comicStepLabels = {
  'queued': 'Đang xếp hàng',
  'comic_storyboard': 'Phân tích truyện & chia cảnh',
  'comic_references': 'Ảnh tham chiếu nhân vật',
  'comic_images': 'Sinh ảnh minh họa',
  'comic_voice': 'Lồng tiếng',
  'comic_clips': 'Dựng clip từng cảnh',
  'comic_export': 'Ghép & xuất video',
  'storyboard_ready': 'Storyboard sẵn sàng để duyệt',
  'completed': 'Đã xuất video',
  'comic_keyframes': 'Sinh keyframe',
  'comic_animation': 'Tạo chuyển động AI',
  'shot_preview_ready': 'Đã dựng xem trước shot',
  'failed': 'Thất bại',
  'cancelled': 'Đã hủy',
  'cancelling': 'Đang dừng',
};

const cameraMotionLabels = {
  'slow_zoom_in': 'Zoom vào chậm',
  'slow_zoom_out': 'Zoom ra chậm',
  'pan_left': 'Lia sang trái',
  'pan_right': 'Lia sang phải',
  'pan_up': 'Lia lên',
  'pan_down': 'Lia xuống',
  'static': 'Đứng yên',
};

const shotTypeLabels = {
  'establishing': 'Toàn cảnh mở đầu',
  'wide': 'Toàn thân',
  'medium': 'Trung cảnh',
  'close_up': 'Cận mặt',
  'extreme_close_up': 'Đặc tả',
  'over_shoulder': 'Qua vai',
};

const shotMovementLabels = {
  'static': 'Đứng yên',
  'push_in': 'Đẩy vào',
  'pull_out': 'Kéo ra',
  'pan_left': 'Lia trái',
  'pan_right': 'Lia phải',
  'tilt_up': 'Ngửa lên',
  'tilt_down': 'Cúi xuống',
  'tracking': 'Bám theo',
  'handheld': 'Cầm tay',
};

const shotAngleLabels = {'eye_level': 'Ngang mắt', 'low': 'Góc thấp', 'high': 'Góc cao', 'overhead': 'Từ trên xuống'};

const animationModeLabels = {'image_to_video': 'Chuyển động theo dự án', 'camera_2_5d': 'Chỉ camera 2.5D'};

const genderLabels = {'female': 'Nữ', 'male': 'Nam', 'unknown': 'Không rõ'};

String comicStepLabel(String? step, String status) => comicStepLabels[step] ?? status;
