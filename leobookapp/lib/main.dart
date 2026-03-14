// main.dart: main.dart: Widget/screen for Unknown.
// Part of LeoBook Unknown
//
// Classes: LeoBookApp

import 'package:flutter/material.dart';
import 'package:leobookapp/core/theme/app_theme_v2.dart';
import 'package:leobookapp/core/theme/theme_cubit.dart';
import 'package:leobookapp/logic/cubit/home_cubit.dart';
import 'package:leobookapp/data/repositories/data_repository.dart';
import 'package:leobookapp/data/repositories/news_repository.dart';
import 'package:leobookapp/presentation/screens/main_screen.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:leobookapp/logic/cubit/user_cubit.dart';

import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:leobookapp/core/config/supabase_config.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Load environment variables
  await dotenv.load(fileName: ".env");

  await Supabase.initialize(
    url: SupabaseConfig.supabaseUrl,
    anonKey: SupabaseConfig.supabaseAnonKey,
  );

  runApp(const LeoBookApp());
}

class LeoBookApp extends StatelessWidget {
  const LeoBookApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiRepositoryProvider(
      providers: [
        RepositoryProvider(create: (context) => DataRepository()),
        RepositoryProvider(create: (context) => NewsRepository()),
      ],
      child: MultiBlocProvider(
        providers: [
          BlocProvider<HomeCubit>(
            create: (context) => HomeCubit(
              context.read<DataRepository>(),
              context.read<NewsRepository>(),
            )..loadDashboard(),
          ),
          BlocProvider<UserCubit>(create: (context) => UserCubit()),
          BlocProvider<ThemeCubit>(create: (_) => ThemeCubit()),
        ],
        child: BlocBuilder<ThemeCubit, ThemeMode>(
          builder: (context, themeMode) => MaterialApp(
            title: 'LeoBook',
            theme: AppThemeV2.lightTheme,
            darkTheme: AppThemeV2.darkTheme,
            themeMode: themeMode,
            home: const MainScreen(),
            debugShowCheckedModeBanner: false,
            builder: (context, child) {
              const double scale = 1;
              final mq = MediaQuery.of(context);
              return Transform.scale(
                scale: scale,
                alignment: Alignment.topLeft,
                child: SizedBox(
                  width: mq.size.width / scale,
                  height: mq.size.height / scale,
                  child: MediaQuery(
                    data: mq.copyWith(
                      size: Size(
                        mq.size.width / scale,
                        mq.size.height / scale,
                      ),
                    ),
                    child: child!,
                  ),
                ),
              );
            },
          ),
        ),
      ),
    );
  }
}
