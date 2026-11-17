# Бизнес-кейсы и покрывающие их тесты

Покрытие здесь мерится **кейсами, а не строками**. Процент покрытия ничего не сказал бы:
половина кода — разбор PDF, где важно поведение на формах, а не задетые строки.

Правило: **каждая строка таблицы обязана иметь живой тест, каждый тест обязан быть
в таблице.** Новая функциональность добавляет строку вместе с тестом. Это проверяется
механически — `test_cases_registry.py` падает, если строка осталась без теста или тест
не описан кейсом.

Формат: `имя_файла::имя_теста`.

| Кейс | Тест |
|---|---|
| Оплата поставщику узнаётся по его счёту из конфига | `test_categorize.py::test_supplier_by_account` |
| Поставщик узнаётся по имени, когда счёт сменился | `test_categorize.py::test_supplier_by_name_when_account_unknown` |
| Налог узнаётся по уплатному счёту публичных приходов | `test_categorize.py::test_tax_by_public_revenue_account` |
| Налог, уплаченный картой через портал | `test_categorize.py::test_tax_via_euprava_portal` |
| Перевод на личный счёт владельца | `test_categorize.py::test_personal_transfer` |
| Взнос при открытии счёта — не выручка и не «прочее» | `test_categorize.py::test_founding_deposit_is_not_income` |
| Динарская противостоимость проданной валюты — выручка | `test_categorize.py::test_income_rsd_is_currency_conversion` |
| Приход и продажа валюты на девизном счёте | `test_categorize.py::test_income_and_fx_on_eur_account` |
| Снятие наличных и покупка картой различаются | `test_categorize.py::test_cash_and_card_are_told_apart` |
| Комиссии банка | `test_categorize.py::test_bank_fee` |
| Неизвестная операция остаётся в `other` и видна | `test_categorize.py::test_unknown_stays_other` |
| Формат сумм банка (`1,234.56`) | `test_parsers.py::test_bank_amount_format` |
| Формат сумм в счёте поставщика (`8.775,00`) | `test_parsers.py::test_supplier_invoice_amount_format` |
| Сербская дата в ISO | `test_parsers.py::test_iso_date_from_serbian` |
| Формат сумм и курса в книге оборота | `test_parsers.py::test_kpo_money_and_rate_formats` |
| Формат сумм в реестре налогов | `test_parsers.py::test_report_money_format_uses_spaces` |
| Номер счёта для импорта: префикс и цифры | `test_parsers.py::test_invoice_number_split_for_bookkeeping` |
| Вид налога по уплатному счёту | `test_parsers.py::test_tax_kind_by_payment_account` |
| Уплатный счёт без ведущих нулей | `test_parsers.py::test_tax_account_without_leading_zeros` |
| Вид налога по назначению, когда счёт неизвестен | `test_parsers.py::test_tax_kind_falls_back_to_purpose` |
| Известная нестрогость разбора назначения | `test_parsers.py::test_tax_purpose_heuristic_is_greedy` |
| Правила проверяются под настоящим именем, симлинк не удваивает находку | `test_clean.py::test_rules_symlink_is_not_scanned_twice` |
| Год, годовая сумма и кварталы берутся из решения об эко-таксе | `test_eco.py::test_decision_gives_year_and_quarters` |
| В решении суммы приходят в двух форматах (`1 220,44` и `300.93`) | `test_eco.py::test_decision_money_formats_differ_in_one_document` |
| Реквизиты уплаты — из фразы про накнаду, а не из таксы на жалобу | `test_eco.py::test_payment_details_are_not_taken_from_appeal_fee` |
| Опомена с тем же префиксом `012_` решением не считается | `test_eco.py::test_opomena_is_not_a_decision` |
| Без решения за год действует аконтација по Q4 прошлого года | `test_eco.py::test_akontacija_falls_back_to_last_quarter` |
| Квартал с истёкшим сроком и без платежа виден как просрочка | `test_eco.py::test_overdue_quarter_is_reported` |
| Годовой платёж одной суммой закрывает все кварталы года | `test_eco.py::test_lump_payment_covers_whole_year` |
| Январский платёж закрывает четвёртый квартал прошлого года | `test_eco.py::test_january_payment_closes_last_years_quarter` |
| Камата и комиссия портала не оплачивают будущий квартал | `test_eco.py::test_overpay_does_not_prepay_future_quarter` |
| Напоминание о пријаве гаснет от файла в `statements/ГГГГ/` | `test_eco.py::test_prijava_reminder_stops_at_marker` |
| Без решений в данных проверка сроков молчит, а не падает | `test_eco.py::test_no_decisions_is_quiet` |
| Налоги не со счёта ИП (личная карта, чужая карта) берутся из `taxes/offbank.csv` | `test_eco.py::test_offbank_payments_are_read_from_data` |
| Опечатка в виде налога в `offbank.csv` — падение, а не потерянный платёж | `test_eco.py::test_offbank_unknown_kind_fails` |
| Без файла платежей не со счёта ИП — пусто, а не ошибка | `test_eco.py::test_offbank_file_is_optional` |
| Квартальная сумма без разделителя тысяч («1073.58») читается целиком | `test_eco.py::test_quarters_without_thousands_separator` |
| Кварталы, не сложившиеся в годовую сумму решения, — падение | `test_eco.py::test_quarters_must_add_up_to_total` |
| Платёж по опомене гасит только главный долг, камата не в счёт квартала | `test_eco.py::test_opomena_payment_counts_principal_only` |
| Позднее решение отодвигает срок доспевших кварталов на 15 дней от решения | `test_eco.py::test_late_decision_moves_due_date` |
| Год уплачен целиком — строка о следующем платеже всё равно есть | `test_eco.py::test_fully_paid_year_still_names_next_payment` |
| Комиссия портала ЛПА (2 %, не меньше 50) не считается налогом | `test_eco.py::test_portal_fee_is_two_percent_with_minimum` |
| Пропуск выписки закрыт добором — данные полные | `test_chain.py::test_gap_closed_when_arithmetic_adds_up` |
| Разрыв открыт: операций в CSV нет | `test_chain.py::test_gap_open_when_operations_are_missing` |
| Разрыв открыт: добор неполный | `test_chain.py::test_gap_open_when_filling_is_partial` |
| Разрыв открыт: CSV ещё не собран | `test_chain.py::test_gap_open_when_csv_absent` |
| Сальдо между выписками считается со знаком, границы не включаются | `test_chain.py::test_ops_between_sums_signed` |
| Запись книги берётся по дате счёта, а не оплаты | `test_kpo.py::test_record_taken_from_invoice_not_payment` |
| Месяц без оплаты в книгу не идёт | `test_kpo.py::test_month_without_payment_is_skipped` |
| Курс берётся из кэша в offline | `test_kpo.py::test_rate_comes_from_cache_in_offline_mode` |
| Без курса запись не считается, а падает | `test_kpo.py::test_offline_mode_refuses_to_guess_missing_rate` |
| Пересчёт в динары округляется half-up | `test_kpo.py::test_rsd_amount_rounds_half_up` |
| Книга не переписывается, если содержимое то же | `test_kpo.py::test_book_not_rewritten_when_content_matches` |
| Отсутствующая книга всегда считается изменившейся | `test_kpo.py::test_missing_book_is_never_considered_same` |
| Текстовый файл не переписывается без изменений | `test_kpo.py::test_write_if_changed_leaves_identical_file_alone` |
| Состав счёта сохраняет формулировки поставщика | `test_payments.py::test_composition_keeps_supplier_wording` |
| Шапка таблицы и числа не попадают в состав | `test_payments.py::test_composition_drops_table_header_and_numbers` |
| Оплата привязывается по номеру счёта в назначении | `test_payments.py::test_payment_matched_by_invoice_number_in_purpose` |
| Счёт без оплаты — законное состояние | `test_payments.py::test_invoice_without_payment_is_not_matched` |
| Оплата без счёта — повод найти документ | `test_payments.py::test_payment_without_invoice_becomes_orphan` |
| Один платёж не привязывается к двум счетам | `test_payments.py::test_one_payment_is_not_reused_for_two_invoices` |
| Категории `fx`, `founding`, `other` не попадают в отчёт | `test_report.py::test_categories_that_must_not_reach_the_report` |
| Затраты разбиваются по поставщикам (проверка на двух) | `test_report.py::test_supplier_breakdown_splits_by_account` |
| Платёж неизвестному поставщику не теряется | `test_report.py::test_unknown_supplier_falls_back_to_counterparty` |
| Приход EUR и его динарская противостоимость — разные колонки | `test_report.py::test_income_split_by_currency` |
| Новый файл-результат подписывается свободно | `test_documents.py::test_ensure_new_allows_missing_file` |
| Подписанный документ не перезаписывается | `test_documents.py::test_ensure_new_refuses_existing` |
| Осознанная перезапись разрешена флагом | `test_documents.py::test_ensure_new_allows_with_force` |
| Контейнер образцов расшифровывается один раз за запуск | `test_documents.py::test_container_decrypted_once_per_process` |
| Отсутствие контейнера объясняет, как его собрать | `test_documents.py::test_missing_container_says_how_to_build` |
| Синтетический PDF пригоден для проверки подписанного | `test_documents.py::test_synthetic_pdf_carries_info_dictionary` |
| Вариант векторной подписи воспроизводится по seed | `test_vector.py::test_same_seed_gives_same_variant` |
| Два подписания дают разные варианты | `test_vector.py::test_two_signings_differ` |
| Плавное поле искажения не сдвигает точку дальше `WARP_AMP` высоты чернил | `test_vector.py::test_field_displacement_within_limit` |
| Поле, наклон и растяжение вместе укладываются в предел из констант | `test_vector.py::test_total_displacement_within_declared_bound` |
| Векторная подпись ложится путями, без картинок и `/XObject` | `test_vector.py::test_vector_signature_adds_paths_not_images` |
| PNG-режим по-прежнему вставляет картинки | `test_vector.py::test_png_signature_still_embeds_images` |
| Векторный режим тоже не перезаписывает подписанное | `test_vector.py::test_vector_signing_refuses_existing_result` |
| Флаг `--png`/`--vector` важнее профиля, без ключа — PNG | `test_vector.py::test_mode_flag_overrides_profile` |
| Векторные шаблоны читаются из контейнера одним вызовом gpg | `test_vector.py::test_vector_container_read_from_memory` |
| Отсутствие контейнера шаблонов объясняет, как его собрать или откатиться | `test_vector.py::test_missing_vector_container_names_the_way_out` |
| Движок не содержит реквизитов, имён и абсолютных путей | `test_portable.py::test_portable_part_is_clean` |
| Жиро-счёт находится без конфига, по одной форме | `test_clean.py::test_form_check_catches_account_without_config` |
| Домашний путь и IMAP-ящик находятся по форме | `test_clean.py::test_form_check_catches_home_path_and_imap` |
| Номер задачи с ключом проекта считается утечкой | `test_clean.py::test_ticket_key_prefix_is_caught` |
| Публичные счета из allow-файла тревоги не поднимают | `test_clean.py::test_allowlist_lets_public_accounts_through` |
| Имена собственные ловятся приватным стоп-листом | `test_clean.py::test_stoplist_from_data_root_catches_names` |
| Отсутствие стоп-листа — не ошибка | `test_clean.py::test_missing_stoplist_is_not_an_error` |
| Реквизиты читаются из конфига и падают понятно | `test_portable.py::test_missing_field_names_the_key` |
| Конфиг подменяется переменной окружения | `test_portable.py::test_profile_path_overridden_by_env` |
| Корень данных задаётся переменной окружения | `test_roots.py::test_data_root_from_env` |
| Корень данных находится поиском вверх от текущего каталога | `test_roots.py::test_data_root_found_upwards` |
| Без данных корнем становится сам движок | `test_roots.py::test_data_root_falls_back_to_code_root` |
| Отсутствие конфига объясняет, что делать | `test_roots.py::test_missing_profile_message_names_the_way_out` |
| Пересборка в клоне без данных не падает | `test_roots.py::test_rebuild_without_data_is_quiet` |
| Корень данных берётся из локальной подсказки движка | `test_roots.py::test_data_root_from_local_hint` |
| Скелет приватного репозитория создаётся одной командой | `test_setup.py::test_init_private_builds_skeleton` |
| Повторная инициализация не переписывает конфиг | `test_setup.py::test_init_private_is_idempotent` |
| Данные внутри движка запрещены | `test_setup.py::test_init_private_refuses_to_use_engine_dir` |
| Доктор перечисляет недостающее вместо падения | `test_setup.py::test_doctor_names_what_is_missing` |
| Синтетический пример собирается и на нём строятся все производные | `test_setup.py::test_examples_build_and_reports_assemble` |
| Образец конфига и фикстура покрывают все обязательные поля | `test_setup.py::test_example_and_fixture_cover_every_required_field` |
| Секций акта в примере столько, сколько языков в конфиге | `test_setup.py::test_example_month_has_a_section_per_act_language` |
| Реквизиты уплатницы берутся из образцов в решении, у каждого года свои | `test_pay_slips.py::test_sample_slips_give_requisites_per_year` |
| Суммы уплатниц — из решений: аконтация пореза и строка месяца доприноса | `test_pay_slips.py::test_amounts_come_from_decisions` |
| Для оплаты наличными шифра 2xx меняется на 1xx | `test_pay_slips.py::test_cash_code_changes_only_first_digit` |
| Строка NBS IPS QR собирается по стандарту | `test_pay_slips.py::test_ips_payload` |
| В сврхе уплатнице — месяц, за который платится | `test_pay_slips.py::test_month_goes_into_purpose` |
| Сврха в IPS QR укладывается в 35 знаков без обрезки посреди слова | `test_pay_slips.py::test_qr_purpose_fits_35` |
