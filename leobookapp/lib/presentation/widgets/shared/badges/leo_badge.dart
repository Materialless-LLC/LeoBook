// leo_badge.dart — LeoBook Design System v2.0
// Part of LeoBook App — Shared Components
//
// Semantic-variant badge for match status, predictions, bets.
// No Bloc/Cubit dependencies.

import 'package:flutter/material.dart';
import '../../../../core/constants/app_colors.dart';
import '../../../../core/constants/spacing_constants.dart';
import '../../../../core/theme/leo_typography.dart';

// ─── Enums ────────────────────────────────────────────────────
enum LeoBadgeVariant { live, finished, scheduled, prediction, betPlaced, custom }
enum LeoBadgeSize    { small, medium, large }

// ─── LeoBadge ─────────────────────────────────────────────────
class LeoBadge extends StatelessWidget {
  final String label;
  final LeoBadgeVariant variant;
  final LeoBadgeSize size;
  final Color? backgroundColor;
  final Color? textColor;
  final Color? borderColor;
  final IconData? icon;

  const LeoBadge({
    super.key,
    required this.label,
    this.variant = LeoBadgeVariant.custom,
    this.size = LeoBadgeSize.medium,
    this.backgroundColor,
    this.textColor,
    this.borderColor,
    this.icon,
  });

  // ── Variant colours ─────────────────────────────────────────
  Color get _bg => switch (variant) {
        LeoBadgeVariant.live       => AppColors.error.withValues(alpha: 0.10),
        LeoBadgeVariant.finished   => AppColors.neutral700,
        LeoBadgeVariant.scheduled  => AppColors.primary.withValues(alpha: 0.10),
        LeoBadgeVariant.prediction => AppColors.secondary.withValues(alpha: 0.10),
        LeoBadgeVariant.betPlaced  => AppColors.success.withValues(alpha: 0.10),
        LeoBadgeVariant.custom     => backgroundColor ?? AppColors.neutral600,
      };

  Color get _fg => switch (variant) {
        LeoBadgeVariant.live       => AppColors.error,
        LeoBadgeVariant.finished   => AppColors.textSecondary,
        LeoBadgeVariant.scheduled  => AppColors.primary,
        LeoBadgeVariant.prediction => AppColors.secondary,
        LeoBadgeVariant.betPlaced  => AppColors.success,
        LeoBadgeVariant.custom     => textColor ?? AppColors.textSecondary,
      };

  Color get _border => switch (variant) {
        LeoBadgeVariant.live       => AppColors.error.withValues(alpha: 0.30),
        LeoBadgeVariant.finished   => AppColors.neutral500,
        LeoBadgeVariant.scheduled  => AppColors.primary.withValues(alpha: 0.30),
        LeoBadgeVariant.prediction => AppColors.secondary.withValues(alpha: 0.30),
        LeoBadgeVariant.betPlaced  => AppColors.success.withValues(alpha: 0.30),
        LeoBadgeVariant.custom     => borderColor ?? AppColors.neutral500,
      };

  // ── Size mappings ───────────────────────────────────────────
  EdgeInsets get _padding => switch (size) {
        LeoBadgeSize.small  => const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        LeoBadgeSize.medium => const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        LeoBadgeSize.large  => const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      };

  TextStyle get _textStyle => switch (size) {
        LeoBadgeSize.small  => LeoTypography.labelSmall,
        LeoBadgeSize.medium => LeoTypography.labelMedium,
        LeoBadgeSize.large  => LeoTypography.labelLarge,
      };

  double get _iconSize => switch (size) {
        LeoBadgeSize.small  => 10.0,
        LeoBadgeSize.medium => 12.0,
        LeoBadgeSize.large  => 14.0,
      };

  @override
  Widget build(BuildContext context) {
    final effectiveIcon = variant == LeoBadgeVariant.live
        ? Icons.circle
        : icon;

    return Container(
      padding: _padding,
      decoration: BoxDecoration(
        color: _bg,
        borderRadius: BorderRadius.circular(SpacingScale.chipRadius),
        border: Border.all(color: _border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (effectiveIcon != null) ...[
            Icon(effectiveIcon, color: _fg, size: _iconSize),
            const SizedBox(width: SpacingScale.xs),
          ],
          Text(
            label,
            style: _textStyle.copyWith(color: _fg),
          ),
        ],
      ),
    );
  }
}
