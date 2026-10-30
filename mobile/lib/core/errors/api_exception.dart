import 'package:dio/dio.dart';

class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  bool get isUnauthorized => statusCode == 401;

  /// FastAPI errors carry `detail` as a string or a list of validation errors.
  factory ApiException.fromDio(DioException error) {
    final response = error.response;
    if (response == null) {
      return const ApiException('Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.');
    }
    final status = response.statusCode;
    final data = response.data;
    String? message;
    if (data is Map) {
      final detail = data['detail'];
      if (detail is String) {
        message = detail;
      } else if (detail is List && detail.isNotEmpty) {
        final first = detail.first;
        message = first is Map ? first['msg']?.toString() : first.toString();
      } else if (data['message'] is String) {
        message = data['message'] as String;
      }
    }
    message ??= switch (status) {
      401 => 'Phiên đăng nhập đã hết hạn.',
      403 => 'Bạn không có quyền thực hiện thao tác này.',
      404 => 'Không tìm thấy dữ liệu.',
      429 => 'Bạn thao tác quá nhanh, vui lòng thử lại sau.',
      _ => 'Máy chủ gặp lỗi ($status).',
    };
    return ApiException(message, statusCode: status);
  }

  @override
  String toString() => message;
}

/// Turns any thrown error into text suitable for the UI.
String errorMessage(Object error) {
  if (error is ApiException) return error.message;
  if (error is DioException) return ApiException.fromDio(error).message;
  return error.toString();
}
