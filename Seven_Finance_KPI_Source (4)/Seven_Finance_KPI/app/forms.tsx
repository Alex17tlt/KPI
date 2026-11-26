"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { defaultPolicy, generateTasks, validatePolicy, type BlockId, type Issue, type MonthRecord, type Policy, type Profile, type Task } from "@/lib/kpi";

export type Action = (action: string, payload?: Record<string, any>) => Promise<boolean>;

const NOTION_URL = "https://app.notion.com/p/KPI-Head-of-Finance-3f3084259c5c807fb336d825ff09108b";
const BLOCK_LABELS: Record<BlockId, string> = { 1: "Ежедневный учёт", 2: "Сверки и выплаты", 3: "Приходы и алерты", 4: "Контроль расходов", 5: "Планирование на 45 дней" };
const CATEGORIES = ["Ежедневный учёт", "Сверки и расчёты", "Выплаты байерам", "Расходы и карты", "Планирование", "Приходы и алерты", "Закрытие месяца", "Другое"];

function monthEnd(month: string): string {
  if (!/^\d{4}-(?:0[1-9]|1[0-2])$/.test(month)) return "";
  return new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0, 12)).toISOString().slice(0, 10);
}

function formatStamp(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", dateStyle: "short", timeStyle: "short" }).format(date);
}

function copyPolicy(policy: Policy): Policy {
  return { ...policy, amounts: { ...policy.amounts }, holidays: [...policy.holidays], workingDates: [...policy.workingDates] };
}

function dateList(raw: string): string[] {
  return [...new Set(raw.split(/[\s,;]+/).map(value => value.trim()).filter(Boolean))].sort();
}

function ErrorText({ children }: { children: string }) {
  return children ? <p className="inline-warning" role="alert">{children}</p> : null;
}

function Modal({ title, children, busy, onClose }: { title: string; children: ReactNode; busy: boolean; onClose: () => void }) {
  const titleId = useId();
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.focus();
    return () => { document.body.style.overflow = previousOverflow; previous?.focus?.(); };
  }, []);

  return <div className="modal-shade" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div ref={dialog} className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
      onKeyDown={event => {
        if (event.key === "Escape" && !busy) { event.preventDefault(); onClose(); }
        if (event.key !== "Tab") return;
        const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
        const first = elements[0];
        const last = elements[elements.length - 1];
        if (!first) { event.preventDefault(); return; }
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) { event.preventDefault(); first.focus(); }
      }}>
      <div className="modal-head"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label="Закрыть окно" onClick={onClose} disabled={busy}>×</button></div>
      {children}
    </div>
  </div>;
}

function ModalActions({ busy, onClose, submit, disabled = false }: { busy: boolean; onClose: () => void; submit: string; disabled?: boolean }) {
  return <div className="modal-actions">
    <button className="button secondary" type="button" onClick={onClose} disabled={busy}>Отмена</button>
    <button className="button primary" type="submit" disabled={busy || disabled}>{busy ? "Сохраняем…" : submit}</button>
  </div>;
}

export function TaskModal({ task, record, canSubmit, busy, onAction, onClose }: {
  task: Task; record: MonthRecord; canSubmit: boolean; busy: boolean; onAction: Action; onClose: () => void;
}) {
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const radioPrefix = useId();
  const submission = record.submissions[task.id];
  const allAnswered = task.checks.every(check => typeof checks[check.key] === "boolean");
  const editable = canSubmit && !record.closure && !submission;
  useEffect(() => { setChecks({}); setComment(""); setError(""); }, [task.id]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!editable || busy) return;
    if (!allAnswered) { setError("Ответьте «Да» или «Нет» на каждую проверку."); return; }
    setError("");
    try { if (await onAction("submit", { taskId: task.id, checks, comment: comment.trim() })) onClose(); }
    catch { setError("Не удалось сохранить отметку. Попробуйте ещё раз."); }
  }

  return <Modal title={task.title} busy={busy} onClose={onClose}>
    <div className="task-modal-summary"><span className="badge neutral">{BLOCK_LABELS[task.block]}</span><p><strong>Срок: {formatStamp(task.deadlineAt)} МСК</strong></p><p>{task.detail}</p></div>
    {submission ? <>
      <div className="notice"><strong>{task.kind === "payment" ? "Выплата отмечена" : "Сдача сохранена"}</strong><p>{formatStamp(submission.at)} МСК</p><p className="helper">Автор и время отметки зафиксированы в журнале.</p></div>
      <div className="checklist">{task.checks.map(check => <div key={check.key}><span>{check.label}</span><span className={`badge ${submission.checks[check.key] ? "yes" : "no"}`}>{submission.checks[check.key] ? "Да" : "Нет"}</span></div>)}</div>
      {submission.comment && <div className="notice"><strong>Комментарий</strong><p style={{ whiteSpace: "pre-wrap" }}>{submission.comment}</p></div>}
      <p className="helper">Показаны ответы и время первой сдачи.</p>
      <div className="modal-actions"><button className="button secondary" type="button" onClick={onClose}>Закрыть</button></div>
    </> : <form onSubmit={submit}>
      <fieldset disabled={!editable || busy} className="checklist">
        <legend>Проверки перед сдачей</legend>
        {task.checks.map(check => <fieldset key={check.key}>
          <legend>{check.label}</legend>
          <div>
            <label className={checks[check.key] === true ? "chosen yes" : "yes"}><input type="radio" name={`${radioPrefix}-${check.key}`} checked={checks[check.key] === true} onChange={() => setChecks(value => ({ ...value, [check.key]: true }))} required /> Да</label>
            <label className={checks[check.key] === false ? "chosen no" : "no"}><input type="radio" name={`${radioPrefix}-${check.key}`} checked={checks[check.key] === false} onChange={() => setChecks(value => ({ ...value, [check.key]: false }))} required /> Нет</label>
          </div>
        </fieldset>)}
      </fieldset>
      <p className="helper">Выберите ответ на каждый пункт. Ответ «Нет» сохранится и будет учтён в расчёте.</p>
      <label className="full">Комментарий или ссылка на результат<textarea value={comment} onChange={event => setComment(event.target.value)} rows={3} maxLength={4000} disabled={!editable || busy} placeholder="Ссылка на таблицу, пояснение к проверке или расхождению" /></label>
      <p className="helper">Время сдачи и автор сохранятся автоматически при нажатии кнопки.</p>
      {!editable && <p className="inline-warning">{record.closure ? "Месяц закрыт для изменений." : "Для сдачи задачи нужна роль исполнителя, редактора или владельца."}</p>}
      <ErrorText>{error}</ErrorText>
      <ModalActions busy={busy} onClose={onClose} submit={task.kind === "payment" ? "Выплачено" : "Сдано"} disabled={!editable || !allAnswered} />
    </form>}
  </Modal>;
}

export function NewMonthModal({ today, busy, onAction, onClose, onCreated }: {
  today: string; busy: boolean; onAction: Action; onClose: () => void; onCreated: (month: string) => void;
}) {
  const [month, setMonth] = useState(today.slice(0, 7));
  const [startDate, setStartDate] = useState(today);
  const [profile, setProfile] = useState<Profile>("notion5");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!monthEnd(month) || !startDate || startDate < `${month}-01` || startDate > monthEnd(month)) { setError("Начало учёта должно находиться в выбранном месяце."); return; }
    setError("");
    try { if (await onAction("createMonth", { month, profile, startDate })) { onCreated(month); onClose(); } }
    catch { setError("Не удалось создать месяц. Попробуйте ещё раз."); }
  }
  return <Modal title="Новый месяц учёта" busy={busy} onClose={onClose}><form onSubmit={submit}>
    <fieldset disabled={busy} className="form-grid">
      <label>Месяц<input type="month" required min="2020-01" max="2100-12" value={month} onChange={event => { const value = event.target.value; setMonth(value); setStartDate(value === today.slice(0, 7) ? today : value ? `${value}-01` : ""); }} /></label>
      <label>Начать учёт с<input type="date" required min={month ? `${month}-01` : "2020-01-01"} max={monthEnd(month)} value={startDate} onChange={event => setStartDate(event.target.value)} /></label>
      <label className="full">Редакция правил<select value={profile} onChange={event => setProfile(event.target.value as Profile)}><option value="notion5">Исходный документ Notion · 5 блоков</option><option value="oleg4">Поздние правки Олега · 4 блока, проект</option></select></label>
    </fieldset>
    <p className="helper">В календарь войдут задачи с выбранной даты начала. У месяца будет собственная копия настроек.</p>
    {profile === "oleg4" && <div className="notice"><strong>Редакция требует подтверждения</strong><p>Блок получения денег исключён. Распределение $800 / $700 / $300 / $200 и часть сроков требуют проверки. Месяц будет создан как проект.</p></div>}
    <ErrorText>{error}</ErrorText><ModalActions busy={busy} onClose={onClose} submit="Создать месяц" />
  </form></Modal>;
}

export function IssueModal({ today, month, busy, onAction, onClose }: {
  today: string; month: string; busy: boolean; onAction: Action; onClose: () => void;
}) {
  const [date, setDate] = useState(today.slice(0, 7) === month ? today : "");
  const [kind, setKind] = useState<Issue["kind"]>("error");
  const [severity, setSeverity] = useState<Issue["severity"] | "">("");
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [foundBy, setFoundBy] = useState("");
  const [foundByDaniel, setFoundByDaniel] = useState(false);
  const [correctedBeforeSending, setCorrectedBeforeSending] = useState(false);
  const [repeat, setRepeat] = useState(false);
  const [repeatEvidence, setRepeatEvidence] = useState("");
  const [error, setError] = useState("");
  const latestDate = monthEnd(month) < today ? monthEnd(month) : today;
  const futureMonth = `${month}-01` > today;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || futureMonth) return;
    if (!date || date.slice(0, 7) !== month || date > today) { setError("Укажите дату внутри выбранного месяца, не позднее сегодняшнего дня."); return; }
    if (!category || !description.trim() || !foundBy.trim() || (kind === "error" && !severity)) { setError("Заполните категорию, описание, автора и уровень ошибки."); return; }
    if (kind === "error" && repeat && !repeatEvidence.trim()) { setError("Укажите дату, ссылку или описание предыдущего случая за три месяца."); return; }
    const fullDescription = description.trim() + (kind === "error" && repeat ? `\n\nПредыдущий случай за три месяца: ${repeatEvidence.trim()}` : "");
    if (fullDescription.length > 4000) { setError("Сократите описание и ссылку на повтор до 4 000 символов вместе."); return; }
    setError("");
    try {
      if (await onAction("issue", { issue: { date, kind, severity: kind === "error" ? severity : "minor", category, description: fullDescription, foundBy: foundBy.trim(), foundByDaniel: kind === "error" && foundByDaniel, correctedBeforeSending: kind === "error" && foundByDaniel && correctedBeforeSending, repeat: kind === "error" && repeat } })) onClose();
    } catch { setError("Не удалось сохранить замечание. Попробуйте ещё раз."); }
  }
  return <Modal title="Добавить замечание" busy={busy} onClose={onClose}><form onSubmit={submit}>
    <fieldset disabled={busy || futureMonth} className="form-grid">
      <label>Дата обнаружения<input type="date" required min={`${month}-01`} max={latestDate} value={date} onChange={event => setDate(event.target.value)} /></label>
      <label>Тип<select value={kind} onChange={event => setKind(event.target.value as Issue["kind"])}><option value="error">Ошибка</option><option value="alert">Пропущенное оповещение о просрочке</option></select></label>
      <label>Категория<select required value={category} onChange={event => setCategory(event.target.value)}><option value="" disabled>Выберите категорию</option>{CATEGORIES.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      {kind === "error" && <label>Уровень ошибки<select required value={severity} onChange={event => setSeverity(event.target.value as Issue["severity"])}><option value="" disabled>Выберите уровень</option><option value="minor">Мелкая · базовый штраф $50</option><option value="significant">Существенная · базовый штраф $200</option><option value="critical">Критическая · базовый штраф $500</option></select></label>}
      <label className="full">Что произошло<textarea required rows={4} maxLength={4000} value={description} onChange={event => setDescription(event.target.value)} placeholder="Конкретная ошибка, где обнаружена, ссылка на подтверждение и последствия" /></label>
      <label className="full">Кто обнаружил<input required type="text" maxLength={200} value={foundBy} onChange={event => setFoundBy(event.target.value)} placeholder="Имя проверяющего" /></label>
      {kind === "error" && <div className="boolean-list full">
        <label><input type="checkbox" checked={foundByDaniel} onChange={event => { setFoundByDaniel(event.target.checked); if (!event.target.checked) setCorrectedBeforeSending(false); }} /> Даниил нашёл ошибку сам</label>
        {foundByDaniel && <label><input type="checkbox" checked={correctedBeforeSending} onChange={event => setCorrectedBeforeSending(event.target.checked)} /> Ошибка исправлена до отправки результата</label>}
        <label><input type="checkbox" checked={repeat} onChange={event => setRepeat(event.target.checked)} /> Повтор такой же ошибки за последние три месяца</label>
        {repeat && <label>Предыдущий случай<textarea required rows={2} maxLength={800} value={repeatEvidence} onChange={event => setRepeatEvidence(event.target.value)} placeholder="Дата и ссылка на предыдущее замечание либо его описание" /></label>}
      </div>}
    </fieldset>
    {kind === "alert" ? <p className="helper">Пропущенное оповещение учитывается в соответствующем блоке KPI. Отдельного денежного штрафа за него нет.</p> : <p className="helper">При подтверждённом повторе базовый штраф удваивается. Самостоятельно найденная и исправленная до отправки ошибка освобождается от штрафа. Срок разбора — два рабочих дня.</p>}
    {futureMonth && <p className="inline-warning">Замечание можно зарегистрировать после наступления выбранного месяца.</p>}
    <ErrorText>{error}</ErrorText><ModalActions busy={busy} onClose={onClose} submit="Сохранить замечание" disabled={futureMonth} />
  </form></Modal>;
}

export function AnalysisModal({ issue, busy, onAction, onClose }: {
  issue: Issue; busy: boolean; onAction: Action; onClose: () => void;
}) {
  const [analysis, setAnalysis] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { setAnalysis(""); setError(""); }, [issue.id]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || issue.voided) return;
    if (!analysis.trim()) { setError("Опишите причину, исправление и новую проверку."); return; }
    setError("");
    try { if (await onAction("analysis", { issueId: issue.id, analysis: analysis.trim() })) onClose(); }
    catch { setError("Не удалось сохранить разбор. Попробуйте ещё раз."); }
  }
  return <Modal title={issue.analysis ? "Дополнить разбор" : "Разбор замечания"} busy={busy} onClose={onClose}><form onSubmit={submit}>
    <div className="task-modal-summary"><span className="badge neutral">{issue.category}</span><p style={{ whiteSpace: "pre-wrap" }}>{issue.description}</p><p className="helper">Обнаружил: {issue.foundBy} · {issue.date}</p></div>
    {issue.analysis && <div className="notice"><strong>Сохранённый разбор</strong><p style={{ whiteSpace: "pre-wrap" }}>{issue.analysis}</p>{issue.analysisAt && <p className="helper">Первая сдача: {formatStamp(issue.analysisAt)} МСК</p>}</div>}
    <label className="full">{issue.analysis ? "Дополнение" : "Причина, исправление и новая проверка"}<textarea required rows={6} maxLength={8000} disabled={busy || issue.voided} value={analysis} onChange={event => setAnalysis(event.target.value)} placeholder={"1. Почему возникла ошибка?\n2. Что исправлено?\n3. Какая проверка предотвратит повтор?"} /></label>
    <p className="helper">Время первого разбора сохраняется автоматически. Дополнения остаются в истории вместе с исходным текстом.</p>
    {issue.voided && <p className="inline-warning">Замечание отменено.</p>}
    <ErrorText>{error}</ErrorText><ModalActions busy={busy} onClose={onClose} submit={issue.analysis ? "Добавить к разбору" : "Сохранить разбор"} disabled={!!issue.voided} />
  </form></Modal>;
}

export function ReasonModal({ action, issueId, busy, onAction, onClose }: {
  action: "close" | "reopen" | "voidIssue"; issueId?: string; busy: boolean; onAction: Action; onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const titles = { close: "Закрыть месяц", reopen: "Открыть месяц для правок", voidIssue: "Отменить замечание" };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!reason.trim()) { setError("Укажите основание действия."); return; }
    if (action === "voidIssue" && !issueId) { setError("Замечание не выбрано. Закройте окно и откройте нужную запись."); return; }
    const payload = action === "close" ? { note: reason.trim() } : action === "reopen" ? { reason: reason.trim() } : { issueId, reason: reason.trim() };
    setError("");
    try { if (await onAction(action, payload)) onClose(); }
    catch { setError("Не удалось сохранить действие. Попробуйте ещё раз."); }
  }
  return <Modal title={titles[action]} busy={busy} onClose={onClose}><form onSubmit={submit}>
    <div className="notice">{action === "close" ? <p>Закрытие фиксирует итог расчёта и блокирует правки месяца. Выплата денег выполняется отдельно.</p> : action === "reopen" ? <p>Сохранённое закрытие останется в журнале. Месяц снова станет доступен для правок, а расчёт — предварительным.</p> : <p>Замечание останется в истории с причиной отмены и будет исключено из расчёта.</p>}</div>
    <label className="full">{action === "close" ? "Основание закрытия и подтверждение проверки" : "Причина"}<textarea required rows={4} maxLength={action === "close" ? 4000 : 2000} disabled={busy} value={reason} onChange={event => setReason(event.target.value)} placeholder={action === "close" ? "Кто проверил расчёт, на каких данных и где зафиксировано согласование" : "Что нужно исправить или почему запись отменяется"} /></label>
    <ErrorText>{error}</ErrorText><ModalActions busy={busy} onClose={onClose} submit={titles[action]} />
  </form></Modal>;
}

export function SettingsPanel({ record, editor, busy, onAction, role, members }: {
  record: MonthRecord; editor: boolean; busy: boolean; onAction: Action; role: string; members: any[];
}) {
  const [policy, setPolicy] = useState<Policy>(() => copyPolicy(record.policy));
  const [holidays, setHolidays] = useState(record.policy.holidays.join("\n"));
  const [workingDates, setWorkingDates] = useState(record.policy.workingDates.join("\n"));
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [memberError, setMemberError] = useState("");
  const canEdit = editor && !record.closure;
  const hasRecords = Object.keys(record.submissions).length > 0 || record.issues.length > 0;
  const blockTotal = ([1, 2, 3, 4, 5] as BlockId[]).reduce((sum, id) => sum + policy.amounts[id], 0);
  useEffect(() => {
    setPolicy(copyPolicy(record.policy)); setHolidays(record.policy.holidays.join("\n")); setWorkingDates(record.policy.workingDates.join("\n")); setReason(""); setError("");
  }, [record.month, record.revision]);

  function changeProfile(profile: Profile) {
    setPolicy(current => ({ ...defaultPolicy(profile, current.startDate), fixed: current.fixed, payrollDay: current.payrollDay, holidays: current.holidays, workingDates: current.workingDates }));
  }
  function timeField(key: "morningTime" | "eveningTime" | "dzTime" | "kzTime" | "paymentTime" | "deadlineTime" | "preparationTime", label: string) {
    return <label key={key}>{label}<input type="time" required value={policy[key]} onChange={event => setPolicy(current => ({ ...current, [key]: event.target.value }))} /></label>;
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canEdit || busy) return;
    if (!reason.trim()) { setError("Укажите причину изменения и основание согласования правил."); return; }
    const next: Policy = { ...policy, holidays: dateList(holidays), workingDates: dateList(workingDates) };
    try {
      validatePolicy(next);
      if (next.holidays.some(date => next.workingDates.includes(date))) throw new Error("Одна дата указана и рабочей, и нерабочей.");
      if (next.startDate > monthEnd(record.month)) throw new Error("Начало учёта позже конца выбранного месяца.");
      if (([1, 2, 3, 4, 5] as BlockId[]).some(id => next.amounts[id] <= 0 && !(next.profile === "oleg4" && id === 3))) throw new Error("Сумма каждого активного блока должна быть больше нуля.");
      generateTasks(record.month, next);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Проверьте значения настроек."); return; }
    setError("");
    try { await onAction("policy", { policy: next, reason: reason.trim() }); }
    catch { setError("Не удалось сохранить настройки. Попробуйте ещё раз."); }
  }
  async function setRole(member: any, newRole: string) {
    const userId = member.userId ?? member.id;
    if (!userId || busy) return;
    setMemberError("");
    try {
      const payload: Record<string, any> = { userId, role: newRole };
      if (typeof member.revision === "number") payload.memberRevision = member.revision;
      await onAction("role", payload);
    } catch { setMemberError("Не удалось изменить роль участника."); }
  }

  return <>
    <section className="panel">
      <div className="section-title"><h2>Правила месяца</h2><span className={`badge ${record.policy.draft ? "neutral" : "yes"}`}>{record.policy.draft ? "Проект правил" : "Применяются в расчёте"}</span></div>
      <p className="helper">Настройки относятся к {record.month}. Каждое сохранение остаётся в истории с автором, временем и причиной.</p>
      <div className="notice"><p><strong>Две редакции источника.</strong> Исходный документ Notion содержит пять блоков. В поздних правках Олега блок получения денег убран; распределение освободившихся $400 и отдельные сроки требуют подтверждения.</p><p>В исходном тексте встречаются 12:00 и 15:00 для утренних данных. Проверьте выбранное время перед применением правил.</p></div>
      {!canEdit && <p className="inline-warning">{record.closure ? "Месяц закрыт. Для изменения правил откройте его с указанием причины." : "Изменять правила могут редактор и владелец."}</p>}
      <form onSubmit={save}>
        <fieldset disabled={!canEdit || busy}>
          <div className="form-grid">
            <label className="full">Редакция<select disabled={hasRecords || !canEdit || busy} value={policy.profile} onChange={event => changeProfile(event.target.value as Profile)}><option value="notion5">Исходный документ Notion · 5 блоков</option><option value="oleg4">Поздние правки Олега · 4 блока</option></select>{hasRecords && <span className="helper">Редакция закреплена: в месяце уже есть отметки или замечания.</span>}</label>
            <label>Фикс за месяц, $<input type="number" min="0" step="0.01" required value={Number.isFinite(policy.fixed) ? policy.fixed : ""} onChange={event => setPolicy(current => ({ ...current, fixed: event.target.valueAsNumber }))} /></label>
            <label>Начало учёта<input type="date" min="2020-01-01" max={monthEnd(record.month)} required value={policy.startDate} onChange={event => setPolicy(current => ({ ...current, startDate: event.target.value }))} /></label>
          </div>
          <h3>Максимальные суммы блоков</h3>
          <div className="form-grid">{([1, 2, 3, 4, 5] as BlockId[]).map(id => <label key={id}>{BLOCK_LABELS[id]}, $<input type="number" min={policy.profile === "oleg4" && id === 3 ? 0 : 0.01} max="2000" step="0.01" required disabled={policy.profile === "oleg4" && id === 3} value={Number.isFinite(policy.amounts[id]) ? policy.amounts[id] : ""} onChange={event => setPolicy(current => ({ ...current, amounts: { ...current.amounts, [id]: event.target.valueAsNumber } }))} />{policy.profile === "oleg4" && id === 3 && <span className="helper">Блок отключён в этой редакции.</span>}</label>)}</div>
          <p className={Number.isFinite(blockTotal) && blockTotal <= 2000 ? "helper" : "inline-warning"}>Общий максимум KPI: {Number.isFinite(blockTotal) ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(blockTotal) : "—"} $ · допустимо до 2 000 $.</p>
          <h3>Сроки по Москве</h3>
          <div className="form-grid">
            {timeField("morningTime", policy.profile === "oleg4" ? "Ежедневный отчёт по ДДС" : "Утренний реестр ДС и ДДС")}
            {policy.profile === "notion5" && timeField("eveningTime", "Вечерние спенды")}
            {policy.profile === "oleg4" && timeField("dzTime", "Сверка дебиторской задолженности")}
            {policy.profile === "oleg4" && timeField("kzTime", "Сверка кредиторской задолженности")}
            {timeField("paymentTime", "Проведение выплат")}
            {timeField("preparationTime", "Подготовка выплат и сверок")}
            {timeField("deadlineTime", "Остальные отчёты и планирование")}
            <label>День расчёта зарплаты<select value={policy.payrollDay ?? ""} onChange={event => setPolicy(current => ({ ...current, payrollDay: event.target.value ? Number(event.target.value) : null }))}><option value="">Не установлен</option>{Array.from({ length: 31 }, (_, index) => index + 1).map(day => <option key={day} value={day}>{day}-е число</option>)}</select></label>
          </div>
          {policy.profile === "oleg4" && <p className="helper">В поздних правках время 16:00 явно указано для выплат 15-го и 20-го. Применение этого времени к остальным выплатам нужно подтвердить.</p>}
          {policy.payrollDay === null && <p className="helper">Пока день расчёта зарплаты не установлен, отдельная задача по зарплате не добавляется.</p>}
          <h3>Рабочий календарь</h3>
          <p className="helper">По умолчанию рабочие дни — с понедельника по пятницу. Дополнительные даты укажите в формате ГГГГ-ММ-ДД, по одной в строке или через запятую.</p>
          <div className="form-grid">
            <label>Дополнительные нерабочие даты<textarea rows={4} value={holidays} onChange={event => setHolidays(event.target.value)} placeholder="ГГГГ-ММ-ДД" spellCheck={false} /></label>
            <label>Дополнительные рабочие даты<textarea rows={4} value={workingDates} onChange={event => setWorkingDates(event.target.value)} placeholder="ГГГГ-ММ-ДД" spellCheck={false} /></label>
          </div>
          <div className="boolean-list"><label><input type="checkbox" checked={!policy.draft} onChange={event => setPolicy(current => ({ ...current, draft: !event.target.checked }))} /> Условия этой редакции проверены</label></div>
          {policy.draft && <p className="helper">При статусе проекта расчёт остаётся предварительным. Перед закрытием месяца подтвердите суммы и сроки.</p>}
          <label className="full">Причина изменения и основание согласования<textarea required rows={3} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} placeholder="Что изменили, кто согласовал, ссылка или дата договорённости" /></label>
        </fieldset>
        <ErrorText>{error}</ErrorText>
        <div className="settings-save"><button className="button primary" type="submit" disabled={!canEdit || busy}>{busy ? "Сохраняем…" : "Сохранить правила месяца"}</button></div>
      </form>
    </section>

    {role === "owner" && <section className="panel">
      <div className="section-title"><h2>Участники и роли</h2></div>
      <p className="helper">Здесь отображаются пользователи, уже вошедшие в инструмент. Новый участник получает доступ на просмотр. Исполнитель сдаёт задачи и разборы; редактор ведёт замечания, правила и закрытие месяца.</p>
      <div className="member-list">{members.length ? <div className="table-scroll"><table className="rules-table"><thead><tr><th scope="col">Участник</th><th scope="col">Роль</th></tr></thead><tbody>{members.map(member => <tr key={member.userId ?? member.id ?? member.email}><td><strong>{member.displayName || member.email}</strong>{member.displayName && member.email !== member.displayName && <div className="helper">{member.email}</div>}</td><td>{member.role === "owner" ? <span className="badge neutral">Владелец</span> : <select aria-label={`Роль: ${member.displayName || member.email}`} value={member.role} disabled={busy || !(member.userId ?? member.id)} onChange={event => void setRole(member, event.target.value)}><option value="viewer">Просмотр</option><option value="contributor">Исполнитель</option><option value="editor">Редактор</option></select>}</td></tr>)}</tbody></table></div> : <p className="helper">Список участников пока пуст.</p>}</div>
      <ErrorText>{memberError}</ErrorText>
    </section>}

    <section className="panel"><div className="section-title"><h2>Описание и источники</h2></div><p>В инструкции разобраны формулы, календарь, ошибки, полномочия участников и порядок закрытия месяца.</p><p className="helper">ДС — фактические остатки денег на счетах, картах, кошельках и в кассе на дату отчёта. ДДС — поступления и выплаты за период. Ожидаемые поступления и дебиторская задолженность показываются отдельно и не прибавляются к остаткам ДС. Ссылку на конкретный рабочий файл нужно закрепить отдельно.</p><div className="modal-actions"><a className="button secondary" href="/documents/Head_of_Finance_KPI_Instruction.pdf" target="_blank" rel="noreferrer">Инструкция PDF</a><a className="button secondary" href="/documents/Head_of_Finance_KPI_Instruction.docx" download>Инструкция Word</a><a className="button secondary" href={NOTION_URL} target="_blank" rel="noreferrer">Исходный Notion</a></div></section>
  </>;
}
