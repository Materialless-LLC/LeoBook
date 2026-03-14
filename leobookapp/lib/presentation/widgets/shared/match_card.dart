// match_card.dart: match_card.dart: Widget/screen for App — Widgets.
// Part of LeoBook App — Widgets
//
// Classes: MatchCard, _MatchCardState, _LiveBadge, _LiveBadgeState

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:leobookapp/data/models/match_model.dart';
import 'package:leobookapp/core/constants/app_colors.dart';
import 'package:leobookapp/core/constants/responsive_constants.dart';
import 'package:leobookapp/data/repositories/data_repository.dart';
import '../../screens/match_details_screen.dart';
import '../../screens/team_screen.dart';
import '../../screens/league_screen.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:leobookapp/core/widgets/glass_container.dart';
import 'badges/leo_badge.dart';
import 'package:leobookapp/core/animations/leo_animations.dart';

class MatchCard extends StatefulWidget {
  final MatchModel match;
  final bool showLiveBadge;
  final bool showLeagueHeader;
  final bool hideLeagueInfo;
  const MatchCard({
    super.key,
    required this.match,
    this.showLiveBadge = true,
    this.showLeagueHeader = true,
    this.hideLeagueInfo = false,
  });

  @override
  State<MatchCard> createState() => _MatchCardState();
}

class _MatchCardState extends State<MatchCard> {
  bool _isHovered = false;

  MatchModel get match => widget.match;
  bool get showLiveBadge => widget.showLiveBadge;
  bool get showLeagueHeader => widget.showLeagueHeader;
  bool get hideLeagueInfo => widget.hideLeagueInfo;

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final isFinished = match.status.toLowerCase().contains('finished') ||
        match.status.toUpperCase() == 'FT';

    // Parse League String "REGION: League"
    String region = "";
    String leagueName = match.league ?? "";
    if (leagueName.contains(':')) {
      final parts = leagueName.split(':');
      if (parts.length >= 2) {
        region = parts[0].trim();
        leagueName = parts[1].trim();
      }
    }
    // Remove "WORLD" hardcoded region labels
    if (region.toUpperCase() == "WORLD") region = "";

    return LeoFadeIn(
      child: Semantics(
        label: '${match.homeTeam} vs ${match.awayTeam}, ${match.status}',
        button: true,
        child: MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: AnimatedScale(
        scale: _isHovered ? 1.012 : 1.0,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOutCubic,
        child: GlassContainer(
          margin: EdgeInsets.symmetric(
            horizontal: Responsive.sp(context, 4),
            vertical: Responsive.sp(context, 4),
          ),
          padding: EdgeInsets.all(Responsive.sp(context, 10)),
          borderRadius: Responsive.sp(context, 10),
          borderColor: _isHovered
              ? AppColors.primary.withValues(alpha: 0.5)
              : ((match.isLive || match.isStartingSoon)
                  ? AppColors.liveRed.withValues(alpha: 0.3)
                  : AppColors.primary.withValues(alpha: 0.2)),
          onTap: () {
            Navigator.push(
              context,
              MaterialPageRoute(
                builder: (context) => MatchDetailsScreen(match: match),
              ),
            );
          },
          child: Stack(
            clipBehavior: Clip.none,
            children: [
              Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (showLeagueHeader && !hideLeagueInfo)
                    GestureDetector(
                      onTap: () {
                        if (match.league != null && match.league!.isNotEmpty) {
                          Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (context) => LeagueScreen(
                                leagueId: match.league!,
                                leagueName: match.league!,
                              ),
                            ),
                          );
                        }
                      },
                      child: Column(
                        children: [
                          // League Crest + League Name Row
                          Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              if (match.leagueCrestUrl != null &&
                                  match.leagueCrestUrl!.isNotEmpty)
                                Padding(
                                  padding: EdgeInsets.only(
                                      right: Responsive.sp(context, 5)),
                                  child: CachedNetworkImage(
                                    imageUrl: match.leagueCrestUrl!,
                                    width: Responsive.sp(context, 14),
                                    height: Responsive.sp(context, 14),
                                    fit: BoxFit.contain,
                                    placeholder: (_, __) => SizedBox(
                                      width: Responsive.sp(context, 14),
                                    ),
                                    errorWidget: (_, __, ___) => SizedBox(
                                      width: Responsive.sp(context, 14),
                                    ),
                                  ),
                                ),
                              Flexible(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    if (region.isNotEmpty)
                                      Text(
                                        region.toUpperCase(),
                                        style: TextStyle(
                                          fontSize: Responsive.sp(context, 7),
                                          fontWeight: FontWeight.w900,
                                          color: AppColors.textGrey,
                                          letterSpacing: 0.5,
                                        ),
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                      ),
                                    Text(
                                      leagueName.toUpperCase(),
                                      style: TextStyle(
                                        fontSize: Responsive.sp(context, 8.5),
                                        fontWeight: FontWeight.w900,
                                        color: Colors.white,
                                        letterSpacing: 0.2,
                                      ),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                          SizedBox(height: Responsive.sp(context, 2)),
                          // Date & Time — hide date when live
                          Text(
                            match.isLive
                                ? "${(match.liveMinute != null && match.liveMinute!.isNotEmpty) ? "${match.liveMinute}'" : 'LIVE'}${match.displayStatus.isEmpty ? '' : ' • ${match.displayStatus}'}"
                                : "${match.date} • ${match.time}${match.displayStatus.isEmpty ? '' : ' • ${match.displayStatus}'}",
                            style: TextStyle(
                              fontSize: Responsive.sp(context, 7),
                              fontWeight: FontWeight.bold,
                              color: match.isLive
                                  ? AppColors.liveRed
                                  : AppColors.textGrey,
                            ),
                          ),
                        ],
                      ),
                    )
                  else ...[
                    Text(
                      match.isLive
                          ? "${(match.liveMinute != null && match.liveMinute!.isNotEmpty) ? "${match.liveMinute}'" : 'LIVE'}${match.displayStatus.isEmpty ? '' : ' • ${match.displayStatus}'}"
                          : "${match.date} • ${match.time}${match.displayStatus.isEmpty ? '' : ' • ${match.displayStatus}'}",
                      style: TextStyle(
                        fontSize: Responsive.sp(context, 7),
                        fontWeight: FontWeight.bold,
                        color: match.isLive
                            ? AppColors.liveRed
                            : AppColors.textGrey,
                      ),
                    ),
                  ],
                  SizedBox(height: Responsive.sp(context, 6)),

                  // Teams Comparison / Result
                  if (isFinished)
                    _buildFinishedLayout(context, isDark)
                  else
                    _buildActiveLayout(context, isDark),

                  SizedBox(height: Responsive.sp(context, 6)),

                  // Prediction Section
                  Container(
                    padding: EdgeInsets.all(Responsive.sp(context, 7)),
                    decoration: BoxDecoration(
                      color: match.isLive
                          ? AppColors.liveRed.withValues(alpha: 0.08)
                          : (isDark
                              ? Colors.white.withValues(alpha: 0.05)
                              : Colors.black.withValues(alpha: 0.03)),
                      borderRadius:
                          BorderRadius.circular(Responsive.sp(context, 8)),
                      border: Border.all(
                        color: match.isLive
                            ? AppColors.liveRed.withValues(alpha: 0.15)
                            : (isDark
                                ? Colors.white.withValues(alpha: 0.06)
                                : Colors.black.withValues(alpha: 0.04)),
                        width: 0.5,
                      ),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Flexible(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                match.isLive
                                    ? "IN-PLAY PREDICTION"
                                    : "LEO PREDICTION",
                                style: TextStyle(
                                  fontSize: Responsive.sp(context, 6),
                                  fontWeight: FontWeight.w900,
                                  color: match.isLive
                                      ? AppColors.liveRed
                                      : AppColors.textGrey,
                                  letterSpacing: 0.3,
                                ),
                              ),
                              SizedBox(height: Responsive.sp(context, 1)),
                              Text(
                                match.prediction ?? "N/A",
                                style: TextStyle(
                                  fontSize: Responsive.sp(context, 9),
                                  fontWeight: FontWeight.w900,
                                  color: isFinished
                                      ? AppColors.success
                                      : AppColors.primary,
                                  decoration: isFinished &&
                                          !(match.prediction
                                                  ?.contains('Accurate') ??
                                              true)
                                      ? TextDecoration.lineThrough
                                      : null,
                                ),
                                overflow: TextOverflow.ellipsis,
                              ),
                              if (match.marketReliability != null)
                                Text(
                                  "RELIABILITY: ${match.marketReliability}%",
                                  style: TextStyle(
                                    fontSize: Responsive.sp(context, 6),
                                    fontWeight: FontWeight.bold,
                                    color: AppColors.success
                                        .withValues(alpha: 0.7),
                                  ),
                                ),
                            ],
                          ),
                        ),
                        SizedBox(width: Responsive.sp(context, 4)),
                        if (match.odds != null)
                          Container(
                            padding: EdgeInsets.symmetric(
                              horizontal: Responsive.sp(context, 8),
                              vertical: Responsive.sp(context, 3),
                            ),
                            decoration: BoxDecoration(
                              color: isFinished
                                  ? Colors.white.withValues(alpha: 0.06)
                                  : AppColors.primary.withValues(alpha: 0.12),
                              borderRadius: BorderRadius.circular(
                                  Responsive.sp(context, 6)),
                              border: Border.all(
                                color: isFinished
                                    ? Colors.white.withValues(alpha: 0.08)
                                    : AppColors.primary.withValues(alpha: 0.25),
                                width: 0.5,
                              ),
                            ),
                            child: Text(
                              match.odds!,
                              style: TextStyle(
                                fontSize: Responsive.sp(context, 10),
                                fontWeight: FontWeight.w900,
                                color: isFinished
                                    ? AppColors.textGrey
                                    : AppColors.primary,
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
              if (showLiveBadge && (match.isLive || match.isStartingSoon))
                Positioned(
                  top: 0,
                  right: 0,
                  child: LeoBadge(
                    label: match.isStartingSoon && !match.isLive
                        ? 'SOON'
                        : (match.liveMinute != null &&
                                match.liveMinute!.isNotEmpty
                            ? "LIVE ${match.liveMinute}'"
                            : 'LIVE'),
                    variant: match.isStartingSoon && !match.isLive
                        ? LeoBadgeVariant.scheduled
                        : LeoBadgeVariant.live,
                    size: LeoBadgeSize.small,
                  ),
                ),
              if (isFinished && match.isPredictionAccurate)
                Positioned(
                  top: 0,
                  right: 0,
                  child: Container(
                    padding: EdgeInsets.symmetric(
                      horizontal: Responsive.sp(context, 6),
                      vertical: Responsive.sp(context, 2),
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.success,
                      borderRadius: BorderRadius.only(
                        topRight: Radius.circular(Responsive.sp(context, 10)),
                        bottomLeft: Radius.circular(Responsive.sp(context, 6)),
                      ),
                    ),
                    child: Text(
                      "ACCURATE",
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: Responsive.sp(context, 6),
                        fontWeight: FontWeight.w900,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildActiveLayout(BuildContext context, bool isDark) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Expanded(child: _buildTeamLogoCol(context, match.homeTeam, isDark)),
        Container(
          padding: EdgeInsets.symmetric(horizontal: Responsive.sp(context, 6)),
          child: match.isLive ||
                  (!match.isNonPlayable &&
                      match.homeScore != null &&
                      match.awayScore != null)
              ? Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          match.homeScore ?? "0",
                          style: TextStyle(
                            fontSize: Responsive.sp(context, 16),
                            fontWeight: FontWeight.w900,
                            color: isDark ? Colors.white : AppColors.textDark,
                          ),
                        ),
                        Padding(
                          padding: EdgeInsets.symmetric(
                              horizontal: Responsive.sp(context, 2)),
                          child: Text(
                            "-",
                            style: TextStyle(
                              color: AppColors.textGrey,
                              fontSize: Responsive.sp(context, 12),
                            ),
                          ),
                        ),
                        Text(
                          match.awayScore ?? "0",
                          style: TextStyle(
                            fontSize: Responsive.sp(context, 16),
                            fontWeight: FontWeight.w900,
                            color: isDark ? Colors.white : AppColors.textDark,
                          ),
                        ),
                      ],
                    ),
                    SizedBox(height: Responsive.sp(context, 1)),
                    if (match.displayStatus.isNotEmpty)
                      Text(
                        match.displayStatus,
                        style: TextStyle(
                          fontSize: Responsive.sp(context, 6),
                          fontWeight: FontWeight.bold,
                          color: match.isLive
                              ? AppColors.liveRed
                              : AppColors.primary,
                          letterSpacing: 0.3,
                        ),
                      ),
                  ],
                )
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      "VS",
                      style: TextStyle(
                        fontSize: Responsive.sp(context, 9),
                        fontWeight: FontWeight.w900,
                        fontStyle: FontStyle.italic,
                        color: AppColors.textGrey,
                      ),
                    ),
                    if (match.isNonPlayable &&
                        match.displayStatus.isNotEmpty) ...[
                      SizedBox(height: Responsive.sp(context, 2)),
                      Text(
                        match.displayStatus,
                        style: TextStyle(
                          fontSize: Responsive.sp(context, 6),
                          fontWeight: FontWeight.bold,
                          color: AppColors.liveRed.withValues(alpha: 0.8),
                        ),
                      ),
                    ],
                  ],
                ),
        ),
        Expanded(child: _buildTeamLogoCol(context, match.awayTeam, isDark)),
      ],
    );
  }

  Widget _buildFinishedLayout(BuildContext context, bool isDark) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Expanded(
          child: Column(
            children: [
              _buildFinishedRow(
                context,
                match.homeTeam,
                match.homeScore ?? "0",
                isDark,
                match.homeCrestUrl,
              ),
              SizedBox(height: Responsive.sp(context, 4)),
              _buildFinishedRow(
                context,
                match.awayTeam,
                match.awayScore ?? "0",
                isDark,
                match.awayCrestUrl,
              ),
            ],
          ),
        ),
        Container(
          width: 0.5,
          height: Responsive.sp(context, 24),
          margin: EdgeInsets.only(left: Responsive.sp(context, 8)),
          color: isDark
              ? Colors.white.withValues(alpha: 0.05)
              : Colors.black.withValues(alpha: 0.04),
        ),
        Container(
          padding: EdgeInsets.only(left: Responsive.sp(context, 8)),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                "RESULT",
                style: TextStyle(
                  fontSize: Responsive.sp(context, 6),
                  fontWeight: FontWeight.w900,
                  color: AppColors.textGrey,
                ),
              ),
              SizedBox(height: Responsive.sp(context, 2)),
              Text(
                "FT",
                style: TextStyle(
                  fontSize: Responsive.sp(context, 11),
                  fontWeight: FontWeight.w900,
                  color: isDark ? Colors.white : AppColors.textDark,
                ),
              ),
              SizedBox(height: Responsive.sp(context, 1)),
              Text(
                match.displayStatus,
                style: TextStyle(
                  fontSize: Responsive.sp(context, 6),
                  fontWeight: FontWeight.bold,
                  color: AppColors.primary,
                  letterSpacing: 0.3,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildFinishedRow(
    BuildContext context,
    String teamName,
    String score,
    bool isDark,
    String? crestUrl,
  ) {
    final logoSize = Responsive.sp(context, 16);
    return GestureDetector(
      onTap: () {
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (context) => TeamScreen(
              teamName: teamName,
              repository: context.read<DataRepository>(),
            ),
          ),
        );
      },
      child: Row(
        children: [
          Container(
            width: logoSize,
            height: logoSize,
            decoration: BoxDecoration(
              color: isDark
                  ? Colors.white.withValues(alpha: 0.05)
                  : AppColors.backgroundLight,
              shape: BoxShape.circle,
            ),
            child: ClipOval(
              child: crestUrl != null && crestUrl.isNotEmpty
                  ? CachedNetworkImage(
                      imageUrl: crestUrl,
                      fit: BoxFit.contain,
                      placeholder: (context, url) => Center(
                        child: Text(
                          teamName.substring(0, 1).toUpperCase(),
                          style: TextStyle(
                            fontSize: Responsive.sp(context, 5),
                            color: AppColors.textGrey,
                          ),
                        ),
                      ),
                      errorWidget: (context, url, error) => Center(
                        child: Text(
                          teamName.substring(0, 1).toUpperCase(),
                          style: TextStyle(
                            fontSize: Responsive.sp(context, 5),
                            color: AppColors.textGrey,
                          ),
                        ),
                      ),
                    )
                  : Center(
                      child: Text(
                        teamName.substring(0, 1).toUpperCase(),
                        style: TextStyle(
                          fontSize: Responsive.sp(context, 6),
                          fontWeight: FontWeight.bold,
                          color: AppColors.textGrey,
                        ),
                      ),
                    ),
            ),
          ),
          SizedBox(width: Responsive.sp(context, 4)),
          Expanded(
            child: Text(
              teamName,
              style: TextStyle(
                fontSize: Responsive.sp(context, 9),
                fontWeight: FontWeight.w700,
                color: isDark ? Colors.white : AppColors.textDark,
              ),
            ),
          ),
          Text(
            score,
            style: TextStyle(
              fontSize: Responsive.sp(context, 11),
              fontWeight: FontWeight.w900,
              color: isDark ? Colors.white : AppColors.textDark,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTeamLogoCol(BuildContext context, String teamName, bool isDark) {
    final crestUrl =
        (teamName == match.homeTeam) ? match.homeCrestUrl : match.awayCrestUrl;
    return GestureDetector(
      onTap: () {
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (context) => TeamScreen(
              teamName: teamName,
              repository: context.read<DataRepository>(),
            ),
          ),
        );
      },
      child: Column(
        children: [
          _buildTeamLogo(context, teamName, isDark, crestUrl),
          SizedBox(height: Responsive.sp(context, 4)),
          Text(
            teamName,
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: Responsive.sp(context, 8),
              fontWeight: FontWeight.w800,
              color: isDark ? Colors.white : AppColors.textDark,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTeamLogo(
      BuildContext context, String teamName, bool isDark, String? crestUrl) {
    final logoSize = Responsive.sp(context, 28);
    return Container(
      width: logoSize,
      height: logoSize,
      decoration: BoxDecoration(
        color: isDark
            ? Colors.white.withValues(alpha: 0.05)
            : Colors.black.withValues(alpha: 0.03),
        shape: BoxShape.circle,
        border: Border.all(
          color: isDark
              ? Colors.white.withValues(alpha: 0.06)
              : Colors.black.withValues(alpha: 0.04),
          width: 0.5,
        ),
      ),
      child: ClipOval(
        child: crestUrl != null && crestUrl.isNotEmpty
            ? CachedNetworkImage(
                imageUrl: crestUrl,
                fit: BoxFit.contain,
                placeholder: (context, url) => Center(
                  child: Text(
                    teamName.substring(0, 1).toUpperCase(),
                    style: TextStyle(
                      fontSize: Responsive.sp(context, 10),
                      fontWeight: FontWeight.w900,
                      color: AppColors.textGrey.withValues(alpha: 0.3),
                    ),
                  ),
                ),
                errorWidget: (context, url, error) => Center(
                  child: Text(
                    teamName.substring(0, 1).toUpperCase(),
                    style: TextStyle(
                      fontSize: Responsive.sp(context, 10),
                      fontWeight: FontWeight.w900,
                      color: AppColors.textGrey.withValues(alpha: 0.3),
                    ),
                  ),
                ),
              )
            : Center(
                child: Text(
                  teamName.substring(0, 1).toUpperCase(),
                  style: TextStyle(
                    fontSize: Responsive.sp(context, 12),
                    fontWeight: FontWeight.w900,
                    color: AppColors.textGrey.withValues(alpha: 0.5),
                  ),
                ),
              ),
      ),
    );
  }
}

// _LiveBadge removed — replaced by LeoBadge(variant: LeoBadgeVariant.live / scheduled)
