import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme/app_theme.dart';
import '../../../../core/auth/session_controller.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/widgets/studio_widgets.dart';
import '../../data/auth_repository.dart';

enum _AuthMode {
  login,
  register,
  resetRequest,
  resetConfirm,
}

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _formKey = GlobalKey<FormState>();

  final _username = TextEditingController();
  final _password = TextEditingController();
  final _displayName = TextEditingController();
  final _registerEmail = TextEditingController();
  final _registerPassword = TextEditingController();
  final _registerConfirm = TextEditingController();
  final _resetEmail = TextEditingController();
  final _resetCode = TextEditingController();
  final _resetNewPassword = TextEditingController();
  final _resetConfirmPassword = TextEditingController();

  _AuthMode _mode = _AuthMode.login;
  bool _remember = true;
  bool _submitting = false;
  bool _obscurePassword = true;
  bool _obscureConfirm = true;
  String? _error;
  String? _infoMessage;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    _displayName.dispose();
    _registerEmail.dispose();
    _registerPassword.dispose();
    _registerConfirm.dispose();
    _resetEmail.dispose();
    _resetCode.dispose();
    _resetNewPassword.dispose();
    _resetConfirmPassword.dispose();
    super.dispose();
  }

  void _switchMode(_AuthMode next, {String? info}) {
    HapticFeedback.selectionClick();
    FocusScope.of(context).unfocus();
    setState(() {
      if (next == _AuthMode.resetRequest &&
          _resetEmail.text.trim().isEmpty &&
          _username.text.contains('@')) {
        _resetEmail.text = _username.text.trim();
      }
      _mode = next;
      _error = null;
      _infoMessage = info;
    });
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      switch (_mode) {
        case _AuthMode.login:
          await ref.read(sessionProvider.notifier).login(
                _username.text.trim(),
                _password.text,
                remember: _remember,
              );
        case _AuthMode.register:
          await ref.read(sessionProvider.notifier).register(
                displayName: _displayName.text.trim(),
                email: _registerEmail.text.trim(),
                password: _registerPassword.text,
              );
        case _AuthMode.resetRequest:
          final email = _resetEmail.text.trim();
          final msg = await ref
              .read(authRepositoryProvider)
              .requestPasswordReset(email);
          if (!mounted) return;
          _resetCode.clear();
          _resetNewPassword.clear();
          _resetConfirmPassword.clear();
          _switchMode(_AuthMode.resetConfirm, info: msg);
        case _AuthMode.resetConfirm:
          final email = _resetEmail.text.trim();
          final msg =
              await ref.read(authRepositoryProvider).confirmPasswordReset(
                    email: email,
                    code: _resetCode.text.trim(),
                    newPassword: _resetNewPassword.text,
                  );
          if (!mounted) return;
          _username.text = email;
          _password.clear();
          _switchMode(_AuthMode.login, info: msg);
      }
    } catch (error) {
      if (mounted) {
        setState(() => _error = errorMessage(error));
      }
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  Future<void> _startGuestTrial() async {
    if (_submitting) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      final guest = await ref.read(authRepositoryProvider).createGuest();
      if (!mounted) return;
      // Show the credentials before signing in: the router leaves this screen
      // as soon as a session exists, and the password cannot be recovered.
      await _showGuestCredentials(guest);
      await ref.read(sessionProvider.notifier).login(guest.email, guest.password);
    } catch (error) {
      if (mounted) {
        setState(() => _error = errorMessage(error));
      }
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  Future<void> _showGuestCredentials(GuestCredentials guest) {
    final text = 'Email: ${guest.email}\nMật khẩu: ${guest.password}';
    return showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Tài khoản dùng thử'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Gói dùng thử: ${guest.quotaMinutes ?? 0} phút video. '
              'Lưu lại thông tin dưới đây để đăng nhập lần sau — '
              'mật khẩu chỉ hiển thị một lần.',
            ),
            const SizedBox(height: 12),
            SelectableText(
              text,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Clipboard.setData(ClipboardData(text: text)),
            child: const Text('Sao chép'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(minimumSize: const Size(0, 40)),
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('Bắt đầu dùng thử'),
          ),
        ],
      ),
    );
  }

  Future<void> _resendResetCode() async {
    final email = _resetEmail.text.trim();
    if (email.isEmpty || _submitting) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      final msg =
          await ref.read(authRepositoryProvider).requestPasswordReset(email);
      if (mounted) {
        setState(() => _infoMessage = msg);
      }
    } catch (error) {
      if (mounted) {
        setState(() => _error = errorMessage(error));
      }
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  InputDecoration _compactInputDecoration({
    required String labelText,
    String? hintText,
    required IconData prefixIcon,
    Widget? suffixIcon,
    required bool compact,
  }) {
    final vPad = compact ? 10.0 : 12.0;
    return InputDecoration(
      isDense: true,
      labelText: labelText,
      hintText: hintText,
      contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: vPad),
      prefixIconConstraints: const BoxConstraints(minWidth: 38, minHeight: 36),
      prefixIcon: Icon(
        prefixIcon,
        size: 18,
        color: AppColors.muted,
      ),
      suffixIconConstraints: const BoxConstraints(minWidth: 38, minHeight: 36),
      suffixIcon: suffixIcon,
    );
  }

  @override
  Widget build(BuildContext context) {
    final keyboardOpen = MediaQuery.viewInsetsOf(context).bottom > 0;

    return Scaffold(
      backgroundColor: const Color(0xFF0B1120),
      resizeToAvoidBottomInset: true,
      body: Stack(
        fit: StackFit.expand,
        children: [
          const _StudioCinematicBackdrop(),
          SafeArea(
            child: LayoutBuilder(
              builder: (context, constraints) {
                final maxH = constraints.maxHeight;
                final maxW = constraints.maxWidth;
                final isMultiField = _mode == _AuthMode.register ||
                    _mode == _AuthMode.resetConfirm;
                final compact = maxH < 730 || isMultiField;
                final ultraCompact = maxH < 640;
                final showBottomPills =
                    !keyboardOpen && (!isMultiField || maxH >= 760) && maxH >= 580;

                return SingleChildScrollView(
                  physics: keyboardOpen
                      ? const ClampingScrollPhysics()
                      : const BouncingScrollPhysics(),
                  padding: EdgeInsets.symmetric(
                    horizontal: maxW < 360 ? 14 : 18,
                    vertical: ultraCompact ? 8 : 12,
                  ),
                  child: ConstrainedBox(
                    constraints: BoxConstraints(
                      minHeight: math.max(
                        0.0,
                        maxH - (ultraCompact ? 16 : 24),
                      ),
                    ),
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 420),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            _buildHeroShowcase(
                              compact: compact,
                              ultraCompact: ultraCompact,
                            ),
                            SizedBox(height: ultraCompact ? 8 : (compact ? 10 : 14)),
                            _buildAuthCard(
                              compact: compact,
                              ultraCompact: ultraCompact,
                              maxW: maxW,
                            ),
                            if (showBottomPills) ...[
                              SizedBox(height: compact ? 10 : 12),
                              _buildBottomStudioHighlights(maxW: maxW),
                            ],
                          ],
                        ),
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeroShowcase({
    required bool compact,
    required bool ultraCompact,
  }) {
    if (ultraCompact) {
      return Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: Colors.white.withValues(alpha: 0.15),
              ),
            ),
            child: const VidTransLogo(size: 30),
          ),
          const SizedBox(width: 10),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: const Color(0x26F97316),
              borderRadius: BorderRadius.circular(999),
              border: Border.all(color: const Color(0x59FB923C)),
            ),
            child: const Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.auto_awesome_rounded,
                  size: 12,
                  color: Color(0xFFFDBA74),
                ),
                SizedBox(width: 5),
                Text(
                  'VIDTRANS CREATOR STUDIO',
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.7,
                    color: Color(0xFFFFEDD5),
                  ),
                ),
              ],
            ),
          ),
        ],
      );
    }

    final logoSize = compact ? 40.0 : 46.0;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          padding: EdgeInsets.all(compact ? 7 : 9),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.07),
            borderRadius: BorderRadius.circular(compact ? 17 : 20),
            border: Border.all(
              color: Colors.white.withValues(alpha: 0.14),
            ),
            boxShadow: [
              BoxShadow(
                color: AppColors.brand.withValues(alpha: 0.30),
                blurRadius: 22,
                spreadRadius: -2,
                offset: const Offset(0, 8),
              ),
            ],
          ),
          child: VidTransLogo(size: logoSize),
        ),
        SizedBox(height: compact ? 8 : 10),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 4),
          decoration: BoxDecoration(
            color: const Color(0x26F97316),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(
              color: const Color(0x59FB923C),
            ),
          ),
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.auto_awesome_rounded,
                size: 12.5,
                color: Color(0xFFFDBA74),
              ),
              SizedBox(width: 5),
              Text(
                'VIDTRANS CREATOR STUDIO',
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.75,
                  color: Color(0xFFFFEDD5),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildAuthCard({
    required bool compact,
    required bool ultraCompact,
    required double maxW,
  }) {
    final isTabMode =
        _mode == _AuthMode.login || _mode == _AuthMode.register;

    final (title, subtitle) = switch (_mode) {
      _AuthMode.login => (
          'Đăng nhập VidTrans',
          'Đăng nhập để truy cập pipeline dịch video, truyện AI và tiến trình xử lý.',
        ),
      _AuthMode.register => (
          'Tạo tài khoản Studio',
          'Kích hoạt ngay gói Free để dịch video, tạo truyện AI và lồng tiếng tự động.',
        ),
      _AuthMode.resetRequest => (
          'Khôi phục mật khẩu',
          'Nhập email đã đăng ký để nhận mã xác nhận gồm 6 chữ số.',
        ),
      _AuthMode.resetConfirm => (
          'Đặt lại mật khẩu mới',
          'Nhập mã 6 chữ số trong email và thiết lập mật khẩu mới.',
        ),
    };

    final hPad = maxW < 360 ? 16.0 : 20.0;
    final vPadTop = compact ? 15.0 : 18.0;
    final vPadBottom = compact ? 14.0 : 16.0;

    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.98),
        borderRadius: BorderRadius.circular(22),
        border: Border.all(
          color: Colors.white.withValues(alpha: 0.85),
          width: 1.2,
        ),
        boxShadow: const [
          BoxShadow(
            color: Color(0x52000000),
            blurRadius: 42,
            spreadRadius: -4,
            offset: Offset(0, 20),
          ),
          BoxShadow(
            color: Color(0x24EA580C),
            blurRadius: 20,
            offset: Offset(0, 6),
          ),
        ],
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          // Top orange-to-teal studio accent bar matching `.auth-card::before`
          Container(
            height: 3.5,
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                colors: [
                  Color(0xFFFB923C),
                  Color(0xFFEA580C),
                  Color(0xFF0D9488),
                ],
              ),
            ),
          ),
          Padding(
            padding: EdgeInsets.fromLTRB(hPad, vPadTop, hPad, vPadBottom),
            child: Form(
              key: _formKey,
              child: AutofillGroup(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      children: [
                        // Scales down instead of overflowing on narrow cards.
                        const Flexible(
                          child: FittedBox(
                            fit: BoxFit.scaleDown,
                            alignment: Alignment.centerLeft,
                            child: StudioEyebrow(
                              label: 'SECURE WORKSPACE',
                              icon: Icons.verified_user_outlined,
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 3,
                          ),
                          decoration: BoxDecoration(
                            color: AppColors.greenSoft,
                            borderRadius: BorderRadius.circular(999),
                            border: Border.all(color: AppColors.greenBorder),
                          ),
                          child: const Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Icons.circle,
                                size: 6,
                                color: AppColors.green,
                              ),
                              SizedBox(width: 4),
                              Text(
                                'Online',
                                style: TextStyle(
                                  fontSize: 10.5,
                                  fontWeight: FontWeight.w700,
                                  color: AppColors.greenText,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    SizedBox(height: compact ? 10 : 12),
                    Text(
                      title,
                      style: TextStyle(
                        fontSize: compact ? 20.5 : 22.5,
                        fontWeight: FontWeight.w900,
                        color: AppColors.ink,
                        letterSpacing: -0.55,
                        height: 1.15,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      subtitle,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: compact ? 12.2 : 12.8,
                        color: AppColors.muted,
                        height: 1.38,
                      ),
                    ),
                    if (isTabMode) ...[
                      SizedBox(height: compact ? 12 : 14),
                      _buildModeSegmentedControl(compact: compact),
                    ],
                    SizedBox(height: compact ? 12 : 14),
                    if (_infoMessage != null) ...[
                      Container(
                        margin: const EdgeInsets.only(bottom: 10),
                        padding: const EdgeInsets.symmetric(
                          horizontal: 11,
                          vertical: 8,
                        ),
                        decoration: BoxDecoration(
                          color: AppColors.tealSoft,
                          borderRadius: BorderRadius.circular(11),
                          border: Border.all(color: AppColors.tealBorder),
                        ),
                        child: Row(
                          children: [
                            const Icon(
                              Icons.check_circle_outline_rounded,
                              size: 16,
                              color: AppColors.teal,
                            ),
                            const SizedBox(width: 7),
                            Expanded(
                              child: Text(
                                _infoMessage!,
                                style: const TextStyle(
                                  color: AppColors.tealText,
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                    ...switch (_mode) {
                      _AuthMode.login => _buildLoginFields(compact: compact),
                      _AuthMode.register => _buildRegisterFields(
                          compact: compact,
                          maxW: maxW,
                        ),
                      _AuthMode.resetRequest =>
                        _buildResetRequestFields(compact: compact),
                      _AuthMode.resetConfirm => _buildResetConfirmFields(
                          compact: compact,
                          maxW: maxW,
                        ),
                    },
                    if (_error != null) ...[
                      const SizedBox(height: 10),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 11,
                          vertical: 8,
                        ),
                        decoration: BoxDecoration(
                          color: AppColors.redSoft,
                          borderRadius: BorderRadius.circular(11),
                          border: Border.all(color: AppColors.redBorder),
                        ),
                        child: Row(
                          children: [
                            const Icon(
                              Icons.error_outline_rounded,
                              size: 16,
                              color: AppColors.red,
                            ),
                            const SizedBox(width: 7),
                            Expanded(
                              child: Text(
                                _error!,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  color: AppColors.redText,
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                    SizedBox(height: compact ? 12 : 14),
                    StudioGradientButton(
                      onPressed: _submitting ? null : _submit,
                      isLoading: _submitting,
                      padding: EdgeInsets.symmetric(
                        horizontal: 18,
                        vertical: compact ? 11.5 : 13,
                      ),
                      icon: switch (_mode) {
                        _AuthMode.login => Icons.login_rounded,
                        _AuthMode.register => Icons.rocket_launch_rounded,
                        _AuthMode.resetRequest => Icons.mark_email_read_outlined,
                        _AuthMode.resetConfirm => Icons.lock_reset_rounded,
                      },
                      label: switch (_mode) {
                        _AuthMode.login => 'Đăng nhập',
                        _AuthMode.register => 'Tạo tài khoản Free',
                        _AuthMode.resetRequest => 'Gửi mã xác nhận',
                        _AuthMode.resetConfirm => 'Đặt lại mật khẩu',
                      },
                    ),
                    SizedBox(height: compact ? 8 : 10),
                    _buildBottomModeLinks(),
                    SizedBox(height: compact ? 6 : 10),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 7,
                      ),
                      decoration: BoxDecoration(
                        color: const Color(0xFFF8FAFC),
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: const Color(0xFFE2E8F0)),
                      ),
                      child: const Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.shield_outlined,
                            size: 13,
                            color: AppColors.muted,
                          ),
                          SizedBox(width: 5),
                          Flexible(
                            child: Text(
                              'OAuth2 Password + JWT · Phiên đăng nhập bảo mật',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                                color: AppColors.muted,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildModeSegmentedControl({required bool compact}) {
    final isLogin = _mode == _AuthMode.login;
    return Container(
      padding: const EdgeInsets.all(3.5),
      decoration: BoxDecoration(
        color: const Color(0xFFF1F5F9),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFE2E8F0)),
      ),
      child: Row(
        children: [
          Expanded(
            child: _SegmentTab(
              label: 'Đăng nhập',
              selected: isLogin,
              compact: compact,
              onTap: () => _switchMode(_AuthMode.login),
            ),
          ),
          const SizedBox(width: 4),
          Expanded(
            child: _SegmentTab(
              label: 'Đăng ký Free',
              selected: !isLogin,
              compact: compact,
              onTap: () => _switchMode(_AuthMode.register),
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _buildLoginFields({required bool compact}) {
    return [
      TextFormField(
        controller: _username,
        keyboardType: TextInputType.emailAddress,
        autocorrect: false,
        autofillHints: const [
          AutofillHints.username,
          AutofillHints.email,
        ],
        textInputAction: TextInputAction.next,
        style: const TextStyle(fontSize: 14),
        decoration: _compactInputDecoration(
          labelText: 'Tên đăng nhập / Email',
          hintText: 'Nhập username hoặc email của bạn',
          prefixIcon: Icons.person_outline_rounded,
          compact: compact,
        ),
        validator: (value) => (value == null || value.trim().isEmpty)
            ? 'Nhập tên đăng nhập hoặc email'
            : null,
      ),
      SizedBox(height: compact ? 10 : 12),
      TextFormField(
        controller: _password,
        obscureText: _obscurePassword,
        autofillHints: const [AutofillHints.password],
        textInputAction: TextInputAction.done,
        onFieldSubmitted: (_) => _submit(),
        style: const TextStyle(fontSize: 14),
        decoration: _compactInputDecoration(
          labelText: 'Mật khẩu',
          hintText: 'Nhập mật khẩu',
          prefixIcon: Icons.lock_outline_rounded,
          compact: compact,
          suffixIcon: IconButton(
            visualDensity: VisualDensity.compact,
            icon: Icon(
              _obscurePassword
                  ? Icons.visibility_outlined
                  : Icons.visibility_off_outlined,
              size: 18,
              color: AppColors.muted,
            ),
            onPressed: () =>
                setState(() => _obscurePassword = !_obscurePassword),
          ),
        ),
        validator: (value) =>
            (value == null || value.isEmpty) ? 'Nhập mật khẩu' : null,
      ),
      SizedBox(height: compact ? 6 : 8),
      Row(
        children: [
          Expanded(
            child: Align(
              alignment: Alignment.centerLeft,
              child: InkWell(
            onTap: () => setState(() => _remember = !_remember),
            borderRadius: BorderRadius.circular(8),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 2, horizontal: 2),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  SizedBox(
                    width: 18,
                    height: 18,
                    child: Checkbox(
                      value: _remember,
                      activeColor: AppColors.brand,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(5),
                      ),
                      onChanged: (v) =>
                          setState(() => _remember = v ?? true),
                    ),
                  ),
                  const SizedBox(width: 7),
                  const Flexible(
                    child: Text(
                      'Ghi nhớ đăng nhập',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                        color: AppColors.inkSoft,
                      ),
                    ),
                  ),
                ],
              ),
            ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          TextButton(
            onPressed: _submitting
                ? null
                : () => _switchMode(_AuthMode.resetRequest),
            style: TextButton.styleFrom(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: const Text(
              'Quên mật khẩu?',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
                color: AppColors.brandDark,
              ),
            ),
          ),
        ],
      ),
    ];
  }

  List<Widget> _buildRegisterFields({
    required bool compact,
    required double maxW,
  }) {
    final gap = compact ? 9.0 : 11.0;
    final pwField = TextFormField(
      controller: _registerPassword,
      obscureText: _obscurePassword,
      textInputAction: TextInputAction.next,
      autofillHints: const [AutofillHints.newPassword],
      style: const TextStyle(fontSize: 13.5),
      decoration: _compactInputDecoration(
        labelText: 'Mật khẩu (≥12 ký tự)',
        hintText: 'Tối thiểu 12 ký tự',
        prefixIcon: Icons.lock_outline_rounded,
        compact: compact,
        suffixIcon: IconButton(
          visualDensity: VisualDensity.compact,
          icon: Icon(
            _obscurePassword
                ? Icons.visibility_outlined
                : Icons.visibility_off_outlined,
            size: 18,
            color: AppColors.muted,
          ),
          onPressed: () =>
              setState(() => _obscurePassword = !_obscurePassword),
        ),
      ),
      validator: (value) => (value == null || value.length < 12)
          ? 'Mật khẩu cần tối thiểu 12 ký tự'
          : null,
    );

    final confirmField = TextFormField(
      controller: _registerConfirm,
      obscureText: _obscureConfirm,
      textInputAction: TextInputAction.done,
      onFieldSubmitted: (_) => _submit(),
      style: const TextStyle(fontSize: 13.5),
      decoration: _compactInputDecoration(
        labelText: 'Nhập lại mật khẩu',
        prefixIcon: Icons.verified_user_outlined,
        compact: compact,
        suffixIcon: IconButton(
          visualDensity: VisualDensity.compact,
          icon: Icon(
            _obscureConfirm
                ? Icons.visibility_outlined
                : Icons.visibility_off_outlined,
            size: 18,
            color: AppColors.muted,
          ),
          onPressed: () =>
              setState(() => _obscureConfirm = !_obscureConfirm),
        ),
      ),
      validator: (value) => value != _registerPassword.text
          ? 'Mật khẩu nhập lại chưa khớp'
          : null,
    );

    return [
      TextFormField(
        controller: _displayName,
        textInputAction: TextInputAction.next,
        autofillHints: const [AutofillHints.name],
        style: const TextStyle(fontSize: 13.5),
        decoration: _compactInputDecoration(
          labelText: 'Tên hiển thị',
          hintText: 'Ví dụ: Review Phim Studio',
          prefixIcon: Icons.badge_outlined,
          compact: compact,
        ),
        validator: (value) => (value == null || value.trim().isEmpty)
            ? 'Nhập tên hiển thị'
            : null,
      ),
      SizedBox(height: gap),
      TextFormField(
        controller: _registerEmail,
        keyboardType: TextInputType.emailAddress,
        autocorrect: false,
        textInputAction: TextInputAction.next,
        autofillHints: const [AutofillHints.email],
        style: const TextStyle(fontSize: 13.5),
        decoration: _compactInputDecoration(
          labelText: 'Email',
          hintText: 'studio@domain.com',
          prefixIcon: Icons.alternate_email_rounded,
          compact: compact,
        ),
        validator: (value) {
          final text = value?.trim() ?? '';
          if (text.isEmpty || !text.contains('@')) {
            return 'Nhập địa chỉ email hợp lệ';
          }
          return null;
        },
      ),
      SizedBox(height: gap),
      pwField,
      SizedBox(height: gap),
      confirmField,
    ];
  }

  List<Widget> _buildResetRequestFields({required bool compact}) {
    return [
      TextFormField(
        controller: _resetEmail,
        keyboardType: TextInputType.emailAddress,
        autocorrect: false,
        textInputAction: TextInputAction.done,
        onFieldSubmitted: (_) => _submit(),
        style: const TextStyle(fontSize: 14),
        decoration: _compactInputDecoration(
          labelText: 'Email đã đăng ký',
          hintText: 'Nhập email tài khoản của bạn',
          prefixIcon: Icons.mail_outline_rounded,
          compact: compact,
        ),
        validator: (value) {
          final text = value?.trim() ?? '';
          if (text.isEmpty || !text.contains('@')) {
            return 'Nhập địa chỉ email hợp lệ';
          }
          return null;
        },
      ),
    ];
  }

  List<Widget> _buildResetConfirmFields({
    required bool compact,
    required double maxW,
  }) {
    final gap = compact ? 9.0 : 11.0;
    return [
      TextFormField(
        controller: _resetCode,
        keyboardType: TextInputType.number,
        maxLength: 6,
        textInputAction: TextInputAction.next,
        style: const TextStyle(fontSize: 13.5),
        decoration: _compactInputDecoration(
          labelText: 'Mã xác nhận (6 chữ số)',
          hintText: 'Nhập mã 6 số trong email',
          prefixIcon: Icons.pin_outlined,
          compact: compact,
        ).copyWith(counterText: ''),
        validator: (value) =>
            (value == null || value.trim().length != 6)
                ? 'Nhập đủ 6 chữ số xác nhận'
                : null,
      ),
      SizedBox(height: gap),
      TextFormField(
        controller: _resetNewPassword,
        obscureText: _obscurePassword,
        textInputAction: TextInputAction.next,
        style: const TextStyle(fontSize: 13.5),
        decoration: _compactInputDecoration(
          labelText: 'Mật khẩu mới (≥12 ký tự)',
          hintText: 'Tối thiểu 12 ký tự',
          prefixIcon: Icons.lock_outline_rounded,
          compact: compact,
          suffixIcon: IconButton(
            visualDensity: VisualDensity.compact,
            icon: Icon(
              _obscurePassword
                  ? Icons.visibility_outlined
                  : Icons.visibility_off_outlined,
              size: 18,
              color: AppColors.muted,
            ),
            onPressed: () =>
                setState(() => _obscurePassword = !_obscurePassword),
          ),
        ),
        validator: (value) => (value == null || value.length < 12)
            ? 'Mật khẩu mới cần tối thiểu 12 ký tự'
            : null,
      ),
      SizedBox(height: gap),
      TextFormField(
        controller: _resetConfirmPassword,
        obscureText: _obscureConfirm,
        textInputAction: TextInputAction.done,
        onFieldSubmitted: (_) => _submit(),
        style: const TextStyle(fontSize: 13.5),
        decoration: _compactInputDecoration(
          labelText: 'Nhập lại mật khẩu mới',
          prefixIcon: Icons.verified_user_outlined,
          compact: compact,
          suffixIcon: IconButton(
            visualDensity: VisualDensity.compact,
            icon: Icon(
              _obscureConfirm
                  ? Icons.visibility_outlined
                  : Icons.visibility_off_outlined,
              size: 18,
              color: AppColors.muted,
            ),
            onPressed: () =>
                setState(() => _obscureConfirm = !_obscureConfirm),
          ),
        ),
        validator: (value) => value != _resetNewPassword.text
            ? 'Mật khẩu nhập lại chưa khớp'
            : null,
      ),
    ];
  }

  Widget _buildBottomModeLinks() {
    final compactStyle = TextButton.styleFrom(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      minimumSize: Size.zero,
      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
    );

    return switch (_mode) {
      // Wrap, not Row: the two links share a line when they fit and stack otherwise.
      _AuthMode.login => Wrap(
          alignment: WrapAlignment.center,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            TextButton(
              style: compactStyle,
              onPressed: _submitting
                  ? null
                  : () => _switchMode(_AuthMode.register),
              child: const Text(
                'Đăng ký',
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w800,
                  color: AppColors.brandDark,
                ),
              ),
            ),
            const Text('·', style: TextStyle(color: AppColors.muted)),
            TextButton.icon(
              style: compactStyle,
              onPressed: _submitting ? null : _startGuestTrial,
              icon: const Icon(Icons.bolt_rounded, size: 16, color: AppColors.brandDark),
              label: const Text(
                'Dùng thử',
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w800,
                  color: AppColors.brandDark,
                ),
              ),
            ),
          ],
        ),
      _AuthMode.register => Center(
          child: TextButton(
            style: compactStyle,
            onPressed:
                _submitting ? null : () => _switchMode(_AuthMode.login),
            child: const Text(
              'Đã có tài khoản? Đăng nhập ngay',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w800,
                color: AppColors.brandDark,
              ),
            ),
          ),
        ),
      _AuthMode.resetRequest => Center(
          child: TextButton.icon(
            style: compactStyle,
            onPressed:
                _submitting ? null : () => _switchMode(_AuthMode.login),
            icon: const Icon(Icons.arrow_back_rounded, size: 15),
            label: const Text(
              'Quay lại đăng nhập',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w800,
                color: AppColors.brandDark,
              ),
            ),
          ),
        ),
      _AuthMode.resetConfirm => Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TextButton(
              style: compactStyle,
              onPressed: _submitting ? null : _resendResetCode,
              child: const Text(
                'Gửi lại mã',
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w800,
                  color: AppColors.brandDark,
                ),
              ),
            ),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 4),
              child: Text('·', style: TextStyle(color: AppColors.muted)),
            ),
            TextButton(
              style: compactStyle,
              onPressed:
                  _submitting ? null : () => _switchMode(_AuthMode.login),
              child: const Text(
                'Quay lại đăng nhập',
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w700,
                  color: AppColors.muted,
                ),
              ),
            ),
          ],
        ),
    };
  }

  /// Single-row responsive studio highlights bar that never wraps into a second
  /// overflowing line on any phone width.
  Widget _buildBottomStudioHighlights({required double maxW}) {
    final isNarrow = maxW < 375;
    final pills = <(IconData, String)>[
      (Icons.translate_rounded, isNarrow ? 'Dịch & Dub AI' : 'Dịch & Lồng tiếng'),
      (Icons.auto_stories_rounded, isNarrow ? 'Truyện AI' : 'Truyện → Video'),
      (Icons.layers_rounded, 'Batch 50 video'),
    ];

    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        for (var i = 0; i < pills.length; i++) ...[
          if (i > 0) const SizedBox(width: 6),
          Flexible(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
                child: Container(
                  padding: EdgeInsets.symmetric(
                    horizontal: isNarrow ? 8 : 10,
                    vertical: 5,
                  ),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.08),
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(
                      color: Colors.white.withValues(alpha: 0.14),
                    ),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        pills[i].$1,
                        size: 12,
                        color: const Color(0xFFFB923C),
                      ),
                      const SizedBox(width: 4),
                      Flexible(
                        child: Text(
                          pills[i].$2,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: isNarrow ? 10.2 : 11,
                            fontWeight: FontWeight.w600,
                            color: Colors.white.withValues(alpha: 0.9),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ],
    );
  }
}

class _SegmentTab extends StatelessWidget {
  const _SegmentTab({
    required this.label,
    required this.selected,
    required this.onTap,
    this.compact = false,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOutCubic,
        padding: EdgeInsets.symmetric(vertical: compact ? 7 : 8),
        decoration: BoxDecoration(
          color: selected ? Colors.white : Colors.transparent,
          borderRadius: BorderRadius.circular(9),
          boxShadow: selected
              ? const [
                  BoxShadow(
                    color: Color(0x140F172A),
                    blurRadius: 8,
                    offset: Offset(0, 2),
                  ),
                ]
              : null,
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: TextStyle(
            fontSize: 12.5,
            fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
            color: selected ? AppColors.brandDark : AppColors.muted,
          ),
        ),
      ),
    );
  }
}

/// Multi-layer cinematic dark studio background matching `.auth-gate` in `web/assets/styles.css`
/// with warm orange & emerald spotlights, subtle studio perspective grid, and audio waveform ribbons.
class _StudioCinematicBackdrop extends StatelessWidget {
  const _StudioCinematicBackdrop();

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            Color(0xFF090F1B),
            Color(0xFF111C2D),
            Color(0xFF162338),
            Color(0xFF0B1320),
          ],
          stops: [0.0, 0.38, 0.72, 1.0],
        ),
      ),
      child: Stack(
        fit: StackFit.expand,
        children: [
          // Top-left warm brand orange radial spotlight
          Positioned(
            top: -130,
            left: -90,
            child: Container(
              width: 390,
              height: 390,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(
                  colors: [
                    const Color(0xFFEA580C).withValues(alpha: 0.34),
                    const Color(0xFFF97316).withValues(alpha: 0.12),
                    Colors.transparent,
                  ],
                  stops: const [0.0, 0.48, 1.0],
                ),
              ),
            ),
          ),
          // Bottom-right emerald/teal studio spotlight
          Positioned(
            bottom: -150,
            right: -100,
            child: Container(
              width: 420,
              height: 420,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(
                  colors: [
                    const Color(0xFF0D9488).withValues(alpha: 0.28),
                    const Color(0xFF14B8A6).withValues(alpha: 0.08),
                    Colors.transparent,
                  ],
                  stops: const [0.0, 0.5, 1.0],
                ),
              ),
            ),
          ),
          // Center-right subtle amber/indigo cinema bloom
          Positioned(
            top: 180,
            right: -70,
            child: Container(
              width: 260,
              height: 260,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(
                  colors: [
                    const Color(0xFFFB923C).withValues(alpha: 0.14),
                    Colors.transparent,
                  ],
                ),
              ),
            ),
          ),
          // Custom painted studio grid, film strips, and audio wave ribbons
          const CustomPaint(
            painter: _StudioBackdropPainter(),
          ),
        ],
      ),
    );
  }
}

class _StudioBackdropPainter extends CustomPainter {
  const _StudioBackdropPainter();

  @override
  void paint(Canvas canvas, Size size) {
    // 1. Subtle architectural studio grid
    final gridPaint = Paint()
      ..color = Colors.white.withValues(alpha: 0.035)
      ..strokeWidth = 1.0;

    const spacing = 38.0;
    for (var x = 0.0; x < size.width; x += spacing) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), gridPaint);
    }
    for (var y = 0.0; y < size.height; y += spacing) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }

    // 2. Flowing studio soundwave / timeline ribbon near top & bottom
    final wavePaintOrange = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.6
      ..shader = LinearGradient(
        colors: [
          Colors.transparent,
          const Color(0xFFFB923C).withValues(alpha: 0.28),
          const Color(0xFF14B8A6).withValues(alpha: 0.22),
          Colors.transparent,
        ],
      ).createShader(Offset.zero & size);

    final topWave = Path();
    final bottomWave = Path();
    for (var x = 0.0; x <= size.width; x += 6) {
      final t = x / size.width;
      final y1 = size.height * 0.14 +
          math.sin(t * math.pi * 2.5) * 22 +
          math.cos(t * math.pi * 5) * 8;
      final y2 = size.height * 0.86 +
          math.cos(t * math.pi * 2.2) * 24 +
          math.sin(t * math.pi * 4.5) * 10;
      if (x == 0) {
        topWave.moveTo(x, y1);
        bottomWave.moveTo(x, y2);
      } else {
        topWave.lineTo(x, y1);
        bottomWave.lineTo(x, y2);
      }
    }
    canvas.drawPath(topWave, wavePaintOrange);
    canvas.drawPath(bottomWave, wavePaintOrange);

    // 3. Subtle audio equalizer bars along the top wave
    final barPaint = Paint()
      ..color = const Color(0xFFFB923C).withValues(alpha: 0.09)
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round;

    for (var i = 1; i < 24; i++) {
      final x = (size.width / 24) * i;
      final h = 6.0 + (math.sin(i * 1.3).abs() * 18.0);
      final cy = size.height * 0.11;
      canvas.drawLine(Offset(x, cy - h), Offset(x, cy + h), barPaint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
