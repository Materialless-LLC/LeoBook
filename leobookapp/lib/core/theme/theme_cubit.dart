// theme_cubit.dart — LeoBook Design System v2.0
// Part of LeoBook App — Core Theme
//
// Description: Cubit managing light/dark ThemeMode for the app.
// Classes: ThemeCubit

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

class ThemeCubit extends Cubit<ThemeMode> {
  ThemeCubit() : super(ThemeMode.dark);

  void toggleTheme() =>
      emit(state == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark);

  void setDark()  => emit(ThemeMode.dark);
  void setLight() => emit(ThemeMode.light);

  bool get isDark => state == ThemeMode.dark;
}
