// leo_animations.dart — LeoBook Design System v2.0
// Part of LeoBook App — Animations
//
// Shared duration/curve tokens + reusable animated widget helpers.

import 'dart:math' as math;

import 'package:flutter/material.dart';

// ─────────────────────────────────────────────────────────────
// Duration tokens
// ─────────────────────────────────────────────────────────────
abstract final class LeoDuration {
  static const Duration micro  = Duration(milliseconds: 100);
  static const Duration short  = Duration(milliseconds: 200);
  static const Duration medium = Duration(milliseconds: 300);
  static const Duration long   = Duration(milliseconds: 500);
  static const Duration xlong  = Duration(milliseconds: 800);
}

// ─────────────────────────────────────────────────────────────
// Curve tokens
// ─────────────────────────────────────────────────────────────
abstract final class LeoCurve {
  static const Curve smooth = Curves.easeInOutCubic;
  static const Curve bouncy = Curves.easeOutBack;
  static const Curve sharp  = Curves.easeOutExpo;
  static const Curve gentle = Curves.easeInOut;
}

// ─────────────────────────────────────────────────────────────
// LeoFadeIn — fades child in on mount, with optional delay
// ─────────────────────────────────────────────────────────────
class LeoFadeIn extends StatefulWidget {
  final Widget child;
  final Duration duration;
  final Duration delay;

  const LeoFadeIn({
    super.key,
    required this.child,
    this.duration = LeoDuration.medium,
    this.delay = Duration.zero,
  });

  @override
  State<LeoFadeIn> createState() => _LeoFadeInState();
}

class _LeoFadeInState extends State<LeoFadeIn>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _opacity;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(vsync: this, duration: widget.duration);
    _opacity = CurvedAnimation(parent: _ctrl, curve: LeoCurve.gentle);

    if (widget.delay == Duration.zero) {
      _ctrl.forward();
    } else {
      Future.delayed(widget.delay, () {
        if (mounted) _ctrl.forward();
      });
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      FadeTransition(opacity: _opacity, child: widget.child);
}

// ─────────────────────────────────────────────────────────────
// LeoSlideIn — slides + fades in from [from] offset
// ─────────────────────────────────────────────────────────────
class LeoSlideIn extends StatefulWidget {
  final Widget child;
  final Duration duration;
  final Duration delay;
  final Offset from;

  const LeoSlideIn({
    super.key,
    required this.child,
    this.duration = LeoDuration.medium,
    this.delay = Duration.zero,
    this.from = const Offset(0, 0.3),
  });

  @override
  State<LeoSlideIn> createState() => _LeoSlideInState();
}

class _LeoSlideInState extends State<LeoSlideIn>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _opacity;
  late final Animation<Offset> _slide;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(vsync: this, duration: widget.duration);
    _opacity = CurvedAnimation(parent: _ctrl, curve: LeoCurve.gentle);
    _slide = Tween<Offset>(begin: widget.from, end: Offset.zero).animate(
      CurvedAnimation(parent: _ctrl, curve: LeoCurve.smooth),
    );

    if (widget.delay == Duration.zero) {
      _ctrl.forward();
    } else {
      Future.delayed(widget.delay, () {
        if (mounted) _ctrl.forward();
      });
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FadeTransition(
        opacity: _opacity,
        child: SlideTransition(position: _slide, child: widget.child),
      );
}

// ─────────────────────────────────────────────────────────────
// LeoScalePulse — 1.0 → 1.05 → 1.0 repeating pulse
// ─────────────────────────────────────────────────────────────
class LeoScalePulse extends StatefulWidget {
  final Widget child;
  final Duration duration;

  const LeoScalePulse({
    super.key,
    required this.child,
    this.duration = LeoDuration.long,
  });

  @override
  State<LeoScalePulse> createState() => _LeoScalePulseState();
}

class _LeoScalePulseState extends State<LeoScalePulse>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _scale;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(vsync: this, duration: widget.duration)
      ..repeat(reverse: true);
    _scale = Tween<double>(begin: 1.0, end: 1.05).animate(
      CurvedAnimation(parent: _ctrl, curve: LeoCurve.gentle),
    );
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      ScaleTransition(scale: _scale, child: widget.child);
}

// ─────────────────────────────────────────────────────────────
// LeoShimmer — animated shimmer loading placeholder
// ─────────────────────────────────────────────────────────────
class LeoShimmer extends StatelessWidget {
  final double width;
  final double height;
  final double borderRadius;

  const LeoShimmer({
    super.key,
    required this.width,
    required this.height,
    this.borderRadius = 8.0,
  });

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: -1.0, end: 2.0),
      duration: const Duration(milliseconds: 1500),
      onEnd: () {},
      builder: (context, value, _) {
        return ClipRRect(
          borderRadius: BorderRadius.circular(borderRadius),
          child: SizedBox(
            width: width,
            height: height,
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.centerLeft,
                  end: Alignment.centerRight,
                  colors: const [
                    Color(0xFF1A1A26),
                    Color(0xFF2A2A3A),
                    Color(0xFF1A1A26),
                  ],
                  stops: [
                    math.max(0.0, value - 0.3),
                    value.clamp(0.0, 1.0),
                    math.min(1.0, value + 0.3),
                  ],
                ),
              ),
            ),
          ),
        );
      },
      child: null,
    );
  }
}

// Repeating shimmer that loops indefinitely using a Listenable rebuild
class _RepeatingShimmer extends StatefulWidget {
  final double width;
  final double height;
  final double borderRadius;

  const _RepeatingShimmer({
    required this.width,
    required this.height,
    required this.borderRadius,
  });

  @override
  State<_RepeatingShimmer> createState() => _RepeatingShimmerState();
}

class _RepeatingShimmerState extends State<_RepeatingShimmer>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _anim;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat();
    _anim = Tween<double>(begin: -1.0, end: 2.0).animate(_ctrl);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _anim,
      builder: (_, __) {
        final v = _anim.value;
        return ClipRRect(
          borderRadius: BorderRadius.circular(widget.borderRadius),
          child: SizedBox(
            width: widget.width,
            height: widget.height,
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.centerLeft,
                  end: Alignment.centerRight,
                  colors: const [
                    Color(0xFF1A1A26),
                    Color(0xFF2A2A3A),
                    Color(0xFF1A1A26),
                  ],
                  stops: [
                    math.max(0.0, v - 0.3),
                    v.clamp(0.0, 1.0),
                    math.min(1.0, v + 0.3),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}
