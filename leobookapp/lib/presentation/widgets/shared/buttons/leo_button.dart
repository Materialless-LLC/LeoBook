// leo_button.dart — LeoBook Design System v2.0
// Part of LeoBook App — Shared Components
//
// Stateful button with press-scale animation, loading/disabled states.
// No Bloc/Cubit dependencies.

import 'package:flutter/material.dart';
import '../../../../core/constants/app_colors.dart';
import '../../../../core/constants/spacing_constants.dart';
import '../../../../core/theme/leo_typography.dart';
import '../../../../core/animations/leo_animations.dart';

// ─── Enums ────────────────────────────────────────────────────
enum LeoButtonVariant { primary, secondary, tertiary }
enum LeoButtonSize { small, medium, large }

// ─── LeoButton ────────────────────────────────────────────────
class LeoButton extends StatefulWidget {
  final String label;
  final VoidCallback? onPressed;
  final LeoButtonVariant variant;
  final LeoButtonSize size;
  final Widget? leadingIcon;
  final bool isLoading;
  final bool fullWidth;

  const LeoButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.variant = LeoButtonVariant.primary,
    this.size = LeoButtonSize.medium,
    this.leadingIcon,
    this.isLoading = false,
    this.fullWidth = false,
  });

  @override
  State<LeoButton> createState() => _LeoButtonState();
}

class _LeoButtonState extends State<LeoButton> {
  bool _pressed = false;

  // ── Size mappings ──────────────────────────────────────────
  EdgeInsets get _padding => switch (widget.size) {
        LeoButtonSize.small  => const EdgeInsets.symmetric(
            horizontal: SpacingScale.md, vertical: SpacingScale.sm),
        LeoButtonSize.medium => const EdgeInsets.symmetric(
            horizontal: SpacingScale.lg, vertical: SpacingScale.md),
        LeoButtonSize.large  => const EdgeInsets.symmetric(
            horizontal: SpacingScale.xl2, vertical: SpacingScale.lg),
      };

  // ── State-resolved colors ──────────────────────────────────
  Color get _bgColor => switch (widget.variant) {
        LeoButtonVariant.primary   => AppColors.primary,
        LeoButtonVariant.secondary => Colors.transparent,
        LeoButtonVariant.tertiary  => Colors.transparent,
      };

  Color get _textColor => switch (widget.variant) {
        LeoButtonVariant.primary   => Colors.white,
        LeoButtonVariant.secondary => AppColors.primary,
        LeoButtonVariant.tertiary  => AppColors.primary,
      };

  Color? get _borderColor => switch (widget.variant) {
        LeoButtonVariant.secondary => AppColors.primary,
        _                          => null,
      };

  // ── Loading indicator color ────────────────────────────────
  Color get _spinnerColor => switch (widget.variant) {
        LeoButtonVariant.primary => Colors.white,
        _                        => AppColors.primary,
      };

  bool get _isEnabled => widget.onPressed != null && !widget.isLoading;

  @override
  Widget build(BuildContext context) {
    final scale = _pressed ? 0.95 : 1.0;

    Widget content = widget.isLoading
        ? SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              valueColor: AlwaysStoppedAnimation<Color>(_spinnerColor),
            ),
          )
        : Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (widget.leadingIcon != null) ...[
                widget.leadingIcon!,
                const SizedBox(width: SpacingScale.sm),
              ],
              Text(
                widget.label,
                style: LeoTypography.labelLarge.copyWith(color: _textColor),
              ),
              if (widget.variant == LeoButtonVariant.tertiary)
                Container(
                  margin: const EdgeInsets.only(top: 1),
                  height: 1,
                  color: _textColor,
                ),
            ],
          );

    Widget button = AnimatedScale(
      scale: scale,
      duration: LeoDuration.short,
      curve: LeoCurve.smooth,
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: SpacingScale.touchTarget),
        child: Opacity(
          opacity: _isEnabled ? 1.0 : 0.4,
          child: GestureDetector(
            onTapDown: (_) => setState(() => _pressed = true),
            onTapUp: (_) {
              setState(() => _pressed = false);
              if (_isEnabled) widget.onPressed?.call();
            },
            onTapCancel: () => setState(() => _pressed = false),
            child: AnimatedContainer(
              duration: LeoDuration.short,
              curve: LeoCurve.smooth,
              padding: _padding,
              decoration: BoxDecoration(
                color: _bgColor,
                borderRadius: BorderRadius.circular(SpacingScale.borderRadius),
                border: _borderColor != null
                    ? Border.all(color: _borderColor!, width: 2)
                    : null,
              ),
              child: Center(child: content),
            ),
          ),
        ),
      ),
    );

    if (widget.fullWidth) {
      button = SizedBox(width: double.infinity, child: button);
    }

    return Semantics(
      button: true,
      enabled: _isEnabled,
      label: widget.label,
      child: button,
    );
  }
}
