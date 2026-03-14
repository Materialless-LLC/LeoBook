// leo_switch.dart — LeoBook Design System v2.0
// Part of LeoBook App — Shared Components
//
// WCAG-compliant switch wrapper with semantic label.
// No Bloc/Cubit dependencies.

import 'package:flutter/material.dart';
import '../../../../core/constants/app_colors.dart';

class LeoSwitch extends StatelessWidget {
  final bool value;
  final ValueChanged<bool> onChanged;
  final String? semanticLabel;

  const LeoSwitch({
    super.key,
    required this.value,
    required this.onChanged,
    this.semanticLabel,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      toggled: value,
      label: semanticLabel ?? 'Toggle switch',
      child: Switch(
        value: value,
        onChanged: onChanged,
        activeColor: AppColors.primary,
        activeTrackColor: AppColors.primaryLight.withValues(alpha: 0.5),
        inactiveThumbColor: AppColors.neutral400,
        inactiveTrackColor: AppColors.neutral600,
      ),
    );
  }
}
