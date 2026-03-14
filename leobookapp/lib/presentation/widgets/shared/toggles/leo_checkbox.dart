// leo_checkbox.dart — LeoBook Design System v2.0
// Part of LeoBook App — Shared Components
//
// WCAG-compliant checkbox wrapper with semantic label.
// No Bloc/Cubit dependencies.

import 'package:flutter/material.dart';
import '../../../../core/constants/app_colors.dart';

class LeoCheckbox extends StatelessWidget {
  final bool? value;
  final ValueChanged<bool?> onChanged;
  final String? semanticLabel;
  final bool tristate;

  const LeoCheckbox({
    super.key,
    required this.value,
    required this.onChanged,
    this.semanticLabel,
    this.tristate = false,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      checked: value ?? false,
      label: semanticLabel ?? 'Checkbox',
      child: Checkbox(
        value: value,
        tristate: tristate,
        onChanged: onChanged,
        activeColor: AppColors.primary,
        checkColor: Colors.white,
        side: const BorderSide(color: AppColors.neutral400, width: 2),
        focusColor: AppColors.primary.withValues(alpha: 0.30),
      ),
    );
  }
}
