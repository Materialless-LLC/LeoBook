// leo_text_field.dart — LeoBook Design System v2.0
// Part of LeoBook App — Shared Components
//
// Animated focus-ring text field with validation support.
// No Bloc/Cubit dependencies.

import 'package:flutter/material.dart';
import '../../../../core/constants/app_colors.dart';
import '../../../../core/constants/spacing_constants.dart';
import '../../../../core/theme/leo_typography.dart';
import '../../../../core/animations/leo_animations.dart';

class LeoTextField extends StatefulWidget {
  final String label;
  final TextEditingController? controller;
  final String? hint;
  final bool obscureText;
  final TextInputType? keyboardType;
  final Widget? prefixIcon;
  final Widget? suffixIcon;
  final String? Function(String?)? validator;
  final void Function(String)? onChanged;
  final int? maxLines;
  final bool enabled;

  const LeoTextField({
    super.key,
    required this.label,
    this.controller,
    this.hint,
    this.obscureText = false,
    this.keyboardType,
    this.prefixIcon,
    this.suffixIcon,
    this.validator,
    this.onChanged,
    this.maxLines = 1,
    this.enabled = true,
  });

  @override
  State<LeoTextField> createState() => _LeoTextFieldState();
}

class _LeoTextFieldState extends State<LeoTextField> {
  late final FocusNode _focus;
  String? _errorText;
  bool _focused = false;

  @override
  void initState() {
    super.initState();
    _focus = FocusNode()
      ..addListener(() {
        setState(() => _focused = _focus.hasFocus);
      });
  }

  @override
  void dispose() {
    _focus.dispose();
    super.dispose();
  }

  Color get _borderColor {
    if (_errorText != null) return AppColors.error;
    if (_focused) return AppColors.primary;
    return AppColors.neutral500;
  }

  double get _borderWidth {
    if (_errorText != null) return 2.0;
    if (_focused) return 2.0;
    return 1.0;
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: widget.label,
      hint: widget.hint,
      value: _errorText,
      child: Opacity(
        opacity: widget.enabled ? 1.0 : 0.5,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // ── Label ──
            Text(
              widget.label,
              style: _focused
                  ? LeoTypography.labelMedium
                      .copyWith(color: AppColors.primary)
                  : LeoTypography.bodyMedium
                      .copyWith(color: AppColors.textSecondary),
            ),
            const SizedBox(height: SpacingScale.xs),

            // ── Animated border container ──
            AnimatedContainer(
              duration: LeoDuration.short,
              curve: LeoCurve.smooth,
              decoration: BoxDecoration(
                color: AppColors.surfaceCard,
                borderRadius:
                    BorderRadius.circular(SpacingScale.borderRadius),
                border: Border.all(
                  color: _borderColor,
                  width: _borderWidth,
                ),
              ),
              child: TextFormField(
                controller: widget.controller,
                focusNode: _focus,
                obscureText: widget.obscureText,
                keyboardType: widget.keyboardType,
                maxLines: widget.obscureText ? 1 : widget.maxLines,
                enabled: widget.enabled,
                style: LeoTypography.bodyLarge
                    .copyWith(color: AppColors.textPrimary),
                onChanged: (v) {
                  if (widget.validator != null) {
                    setState(() => _errorText = widget.validator!(v));
                  }
                  widget.onChanged?.call(v);
                },
                decoration: InputDecoration(
                  hintText: widget.hint,
                  hintStyle: LeoTypography.bodyMedium
                      .copyWith(color: AppColors.textDisabled),
                  prefixIcon: widget.prefixIcon,
                  suffixIcon: widget.suffixIcon,
                  contentPadding: const EdgeInsets.all(SpacingScale.md),
                  border: InputBorder.none,
                  enabledBorder: InputBorder.none,
                  focusedBorder: InputBorder.none,
                  errorBorder: InputBorder.none,
                  disabledBorder: InputBorder.none,
                ),
              ),
            ),

            // ── Error message ──
            if (_errorText != null) ...[
              const SizedBox(height: SpacingScale.xs),
              Semantics(
                liveRegion: true,
                child: Text(
                  _errorText!,
                  style: LeoTypography.bodySmall
                      .copyWith(color: AppColors.error),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
