// glass_card.dart — LeoBook Design System v2.0
// Part of LeoBook App — Shared Components
//
// Backdrop-blurred glass card. Supersedes raw GlassContainer for card surfaces.
// No Bloc/Cubit dependencies.

import 'dart:ui';
import 'package:flutter/material.dart';
import '../../../../core/constants/app_colors.dart';
import '../../../../core/constants/spacing_constants.dart';
import '../../../../core/animations/leo_animations.dart';

class GlassCard extends StatefulWidget {
  final Widget child;
  final EdgeInsetsGeometry? padding;
  final double borderRadius;
  final double blurRadius;
  final Color? backgroundColor;
  final Color? borderColor;
  final VoidCallback? onTap;
  final double? width;
  final double? height;

  const GlassCard({
    super.key,
    required this.child,
    this.padding,
    this.borderRadius = SpacingScale.cardRadius,
    this.blurRadius = 12.0,
    this.backgroundColor,
    this.borderColor,
    this.onTap,
    this.width,
    this.height,
  });

  @override
  State<GlassCard> createState() => _GlassCardState();
}

class _GlassCardState extends State<GlassCard> {
  bool _pressed = false;

  double get _scale => (widget.onTap != null && _pressed) ? 0.98 : 1.0;

  @override
  Widget build(BuildContext context) {
    final bg = widget.backgroundColor ?? AppColors.glass;
    final border = widget.borderColor ?? AppColors.glassBorder;
    final pad = widget.padding ??
        const EdgeInsets.all(SpacingScale.cardPadding);

    Widget inner = AnimatedContainer(
      duration: LeoDuration.short,
      curve: LeoCurve.smooth,
      width: widget.width,
      height: widget.height,
      padding: pad,
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(widget.borderRadius),
        border: Border.all(color: border, width: 1.0),
      ),
      child: widget.child,
    );

    Widget card = ClipRRect(
      borderRadius: BorderRadius.circular(widget.borderRadius),
      child: BackdropFilter(
        filter: ImageFilter.blur(
          sigmaX: widget.blurRadius,
          sigmaY: widget.blurRadius,
        ),
        child: inner,
      ),
    );

    if (widget.onTap != null) {
      card = GestureDetector(
        onTapDown: (_) => setState(() => _pressed = true),
        onTapUp: (_) {
          setState(() => _pressed = false);
          widget.onTap!();
        },
        onTapCancel: () => setState(() => _pressed = false),
        child: AnimatedScale(
          scale: _scale,
          duration: LeoDuration.short,
          curve: LeoCurve.smooth,
          child: card,
        ),
      );

      return Semantics(button: true, child: card);
    }

    return card;
  }
}
