// leo_chip.dart — LeoBook Design System v2.0
// Part of LeoBook App — Shared Components
//
// Filterable/selectable chip with optional delete affordance.
// No Bloc/Cubit dependencies.

import 'package:flutter/material.dart';
import '../../../../core/constants/app_colors.dart';
import '../../../../core/constants/spacing_constants.dart';
import '../../../../core/theme/leo_typography.dart';

class LeoChip extends StatelessWidget {
  final String label;
  final IconData? icon;
  final VoidCallback? onDelete;
  final VoidCallback? onTap;
  final Color? backgroundColor;
  final Color? textColor;
  final bool selected;

  const LeoChip({
    super.key,
    required this.label,
    this.icon,
    this.onDelete,
    this.onTap,
    this.backgroundColor,
    this.textColor,
    this.selected = false,
  });

  Color get _bg => selected
      ? AppColors.primary.withValues(alpha: 0.20)
      : (backgroundColor ?? AppColors.neutral700);

  Color get _fg => selected
      ? AppColors.primary
      : (textColor ?? AppColors.textSecondary);

  Color get _border =>
      selected ? AppColors.primary : AppColors.neutral600;

  @override
  Widget build(BuildContext context) {
    final semanticLabel = [
      label,
      if (selected) 'selected',
      if (onDelete != null) 'double-tap to remove',
    ].join(', ');

    return Semantics(
      label: semanticLabel,
      button: onTap != null,
      child: GestureDetector(
        onTap: onTap,
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: SpacingScale.touchTarget),
          child: Container(
            padding: const EdgeInsets.symmetric(
              horizontal: SpacingScale.md,
              vertical: SpacingScale.sm,
            ),
            decoration: BoxDecoration(
              color: _bg,
              borderRadius: BorderRadius.circular(SpacingScale.chipRadius),
              border: Border.all(color: _border),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (icon != null) ...[
                  Icon(icon, color: _fg, size: 16),
                  const SizedBox(width: SpacingScale.sm),
                ],
                Text(
                  label,
                  style: LeoTypography.labelMedium.copyWith(color: _fg),
                ),
                if (onDelete != null) ...[
                  const SizedBox(width: SpacingScale.xs),
                  GestureDetector(
                    onTap: onDelete,
                    child: Icon(Icons.close, size: 14, color: _fg),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
