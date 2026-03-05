# LeoBook App — High Density Liquid Glass Dashboard

**Developer**: Materialless LLC
**Chief Engineer**: Emenike Chinenye James
**Goal**: Ship extreme UI density with premium translucency and zero hardcoded overflows.

---

## Design Philosophy

The app follows the **"Telegram Aesthetic"** — maximizing information density without sacrificing visual elegance.

### 1. High Density UI
- **80% Size Reduction**: All elements (icons, text, cards) scaled to professional "elite" proportions.
- **Micro-Radii**: Border radii of 14dp for a sharp, modern edge.
- **Glass-First**: 60% translucency `BackdropFilter` containers with 16 sigma blur.

### 2. Proportional Scaling (Constraints-Based Design)
Hardcoded pixel values are eliminated.
- **`Responsive.sp(context, value)`**: Scales based on viewport width relative to a 375dp reference.
- **`Responsive.dp(context, value)`**: Handles desktop-specific scaling (1440dp reference).
- **Parity**: Mobile, Web, and Desktop share the same responsive foundation.

---

## Core Features

- **Liquid Glass Containers**: Premium frosted effects using the `GlassContainer` widget.
- **4-Tab Match System**: `ALL` | `LIVE` | `FINISHED` | `SCHEDULED` tabs with automatic filtering.
- **Real-Time Streaming**: Syncs with `fs_live_streamer.py` output for minute-by-minute updates via Supabase.
- **Accuracy Report Cards**: Dynamic per-league accuracy sorted by match count (descending) then accuracy percentage (descending).
- **Match Status Display**: Correctly identifies FT, FT (AET), FT (Pen) via exact status matching — avoids false positives from substring traps.
- **Double Chance Accuracy**: Supports team-name-based OR patterns (e.g., "Arsenal or Liverpool" → accurate if not a draw).
- **Constraints-Based Layout**: All sizing uses `Responsive.sp()`, `FractionallySizedBox`, `LayoutBuilder`, and `Flexible` widgets.

---

## Technical Stack

| Layer | Technology |
|-------|-----------|
| **State Management** | `flutter_bloc` / `Cubit` (HomeCubit, UserCubit, SearchCubit) |
| **Architecture** | Clean Architecture (Data → Logic → Presentation) |
| **Responsive System** | Custom `lib/core/constants/responsive_constants.dart` |
| **Backend** | Supabase (push-only read mirror from local SQLite) |
| **Font** | Google Fonts — Lexend |
| **Theme** | Material 3, Liquid Glass dark mode |

---

## Project Structure

```
leobookapp/lib/
├── core/
│   ├── config/             # Supabase config
│   ├── constants/          # Responsive, theme, colors
│   └── animations/         # LiquidFadeIn, transitions
├── data/
│   ├── models/             # MatchModel, UserModel, etc.
│   ├── repositories/       # DataRepository, NewsRepository
│   └── services/           # Supabase data service
├── logic/
│   └── cubit/              # HomeCubit, UserCubit, SearchCubit
└── presentation/
    ├── screens/            # Pure viewport dispatchers
    └── widgets/
        ├── desktop/        # Desktop-only widgets
        ├── mobile/         # Mobile-only widgets
        └── shared/         # Cross-platform reusable widgets
```

---

## Development Commands

```powershell
flutter pub get
flutter analyze                            # Must return 0 issues
flutter run -d chrome                      # Web preview
flutter run                                # Mobile preview
flutter build web --release --web-renderer canvaskit  # Production web build
flutter build apk --release                # Android APK
```

### Golden Rules
> **Rule**: Never use a fixed `double` for layout-critical spacing. Use `Responsive.sp()` or `Responsive.horizontalPadding()`.

> **Rule**: Screens are pure dispatchers — they select `DesktopHomeContent` or `MobileHomeContent` based on viewport width. Zero inline layout.

> **Rule**: `flutter analyze` must return 0 issues before every commit.

---

*Last updated: March 1, 2026*
*Materialless LLC*
