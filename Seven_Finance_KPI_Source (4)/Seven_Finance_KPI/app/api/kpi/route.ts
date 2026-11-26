import { calculateMonth, emptyMonth, generateTasks, type Issue, type Submission } from "@/lib/kpi";
import { ApiError, CONTRIBUTORS, EDITORS, authenticate, bool, changeRole, dateValue, ensureOrigin, errorResponse, getMonth, insertMonth, lastDay, monthValue, object, onlyKeys, policyValue, profileValue, requestBody, requireRole, response, revisionValue, saveMonth, snapshot, text, today, type Role } from "@/lib/server";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const context = await authenticate();
    return response(await snapshot(context));
  } catch (error) { return errorResponse(error); }
}

export async function POST(request: Request) {
  try {
    ensureOrigin(request);
    const context = await authenticate();
    const payload = await requestBody(request);
    const action = text(payload.action, "Действие", 40);

    if (action === "role") {
      await changeRole(context, payload);
      return response(await snapshot(context));
    }

    const month = monthValue(payload.month);
    if (action === "createMonth") {
      requireRole(context, EDITORS);
      onlyKeys(payload, ["action", "month", "revision", "profile", "startDate"]);
      const profile = profileValue(payload.profile);
      const startDate = payload.startDate === undefined ? (month === today().slice(0, 7) ? today() : `${month}-01`) : dateValue(payload.startDate, "Начало учёта");
      if (startDate > lastDay(month)) throw new ApiError(400, "Начало учёта не может быть позже конца месяца.");
      const record = emptyMonth(month, profile, startDate);
      generateTasks(month, record.policy);
      await insertMonth(context, record);
      return response(await snapshot(context), 201);
    }

    const allowed: Role[] = action === "submit" || action === "analysis" ? CONTRIBUTORS : EDITORS;
    requireRole(context, allowed);
    const revision = revisionValue(payload.revision);
    const record = await getMonth(context, month);
    if (record.revision !== revision) throw new ApiError(409, "Месяц уже изменён в другой вкладке или другим участником. Обновите страницу.");
    if (record.closure && action !== "reopen") throw new ApiError(409, "Месяц закрыт. Для правок его нужно открыть с указанием причины.");
    const at = new Date().toISOString();
    let reason = "";
    let details: unknown;
    const baseKeys = ["action", "month", "revision"];

    switch (action) {
      case "policy": {
        onlyKeys(payload, [...baseKeys, "policy", "reason"]);
        reason = text(payload.reason, "Причина изменения правил", 2000);
        const policy = policyValue(payload.policy, month);
        if (policy.profile !== record.policy.profile && (Object.keys(record.submissions).length || record.issues.length)) throw new ApiError(409, "После начала учёта нельзя переключить редакцию: уже есть отметки или замечания.");
        const tasks = new Set(generateTasks(month, policy).map(task => task.id));
        if (Object.keys(record.submissions).some(id => !tasks.has(id))) throw new ApiError(409, "Изменение календаря удалит задачу с сохранённой отметкой. Сохраните её дату и начало учёта.");
        details = { before: record.policy, after: policy };
        record.policy = policy;
        break;
      }
      case "submit": {
        onlyKeys(payload, [...baseKeys, "taskId", "checks", "comment"]);
        const taskId = text(payload.taskId, "Задача", 200);
        const task = generateTasks(month, record.policy).find(item => item.id === taskId);
        if (!task) throw new ApiError(400, "Задача не относится к этому месяцу или текущим правилам.");
        if (Object.hasOwn(record.submissions, taskId)) throw new ApiError(409, "Эта задача уже сдана. Первая дата и ответы сохранены.");
        const rawChecks = object(payload.checks, "Проверки");
        onlyKeys(rawChecks, task.checks.map(check => check.key), "Проверки");
        const checks: Record<string, boolean> = {};
        for (const check of task.checks) checks[check.key] = bool(rawChecks[check.key], check.label);
        const submission: Submission = { taskId, at, checks, comment: payload.comment === undefined ? "" : text(payload.comment, "Комментарий", 4000, false), by: context.user.userId };
        record.submissions[taskId] = submission;
        details = { taskId, after: submission };
        break;
      }
      case "issue": {
        onlyKeys(payload, [...baseKeys, "issue"]);
        const raw = object(payload.issue, "Замечание");
        onlyKeys(raw, ["date", "kind", "severity", "category", "description", "foundBy", "foundByDaniel", "correctedBeforeSending", "repeat"], "Замечание");
        const date = dateValue(raw.date, "Дата замечания");
        if (date.slice(0, 7) !== month) throw new ApiError(400, "Дата замечания должна находиться в выбранном месяце.");
        if (date > today()) throw new ApiError(400, "Замечание нельзя датировать будущим числом.");
        if (raw.kind !== "error" && raw.kind !== "alert") throw new ApiError(400, "Укажите тип замечания.");
        if (!["minor", "significant", "critical"].includes(raw.severity as string)) throw new ApiError(400, "Укажите уровень ошибки.");
        const issue: Issue = {
          id: crypto.randomUUID(), date, kind: raw.kind, severity: raw.severity as Issue["severity"],
          category: text(raw.category, "Категория", 120), description: text(raw.description, "Описание", 4000),
          foundBy: text(raw.foundBy, "Кто обнаружил", 200), foundByDaniel: bool(raw.foundByDaniel, "Найдена Даниилом"),
          correctedBeforeSending: bool(raw.correctedBeforeSending, "Исправлена до отправки"), repeat: bool(raw.repeat, "Повтор за три месяца"),
          analysis: "", analysisAt: null, createdAt: at,
        };
        record.issues.push(issue);
        details = { after: issue };
        break;
      }
      case "analysis": {
        onlyKeys(payload, [...baseKeys, "issueId", "analysis"]);
        const issueId = text(payload.issueId, "Замечание", 200);
        const issue = record.issues.find(item => item.id === issueId);
        if (!issue) throw new ApiError(404, "Замечание не найдено.");
        if (issue.voided) throw new ApiError(409, "Замечание отменено; разбор больше не требуется.");
        const analysis = text(payload.analysis, "Разбор: причина и меры", 8000);
        const previous = { analysis: issue.analysis, analysisAt: issue.analysisAt };
        issue.analysis = issue.analysis ? `${issue.analysis}\n\nДополнение ${at}\n${analysis}` : analysis;
        if (issue.analysis.length > 20_000) throw new ApiError(400, "Разбор слишком длинный. Сократите дополнение.");
        // A later supplement must not move the original analysis deadline result.
        issue.analysisAt = issue.analysisAt ?? at;
        details = { issueId, before: previous, after: { analysis: issue.analysis, analysisAt: issue.analysisAt }, submittedAt: at };
        reason = "Разбор замечания или дополнение к нему";
        break;
      }
      case "voidIssue": {
        onlyKeys(payload, [...baseKeys, "issueId", "reason"]);
        reason = text(payload.reason, "Причина отмены замечания", 2000);
        const issueId = text(payload.issueId, "Замечание", 200);
        const issue = record.issues.find(item => item.id === issueId);
        if (!issue) throw new ApiError(404, "Замечание не найдено.");
        if (issue.voided) throw new ApiError(409, "Замечание уже отменено.");
        details = { issueId, before: { ...issue }, after: { ...issue, voided: true } };
        issue.voided = true;
        break;
      }
      case "revenue": {
        onlyKeys(payload, [...baseKeys, "value", "evidence"]);
        if (payload.value !== null && (typeof payload.value !== "number" || !Number.isFinite(payload.value) || payload.value < 0 || payload.value > 100)) throw new ApiError(400, "Доля поступлений должна быть от 0 до 100% либо пустой.");
        const evidence = payload.evidence === undefined ? "" : text(payload.evidence, "Основание расчёта", 4000, false);
        if (payload.value !== null && !evidence) throw new ApiError(400, "Добавьте основание: ссылку на расчёт или пояснение числителя и знаменателя.");
        details = { before: { value: record.revenuePercent, evidence: record.revenueEvidence }, after: { value: payload.value, evidence } };
        record.revenuePercent = payload.value as number | null;
        record.revenueEvidence = evidence;
        break;
      }
      case "close": {
        onlyKeys(payload, [...baseKeys, "note"]);
        if (today().slice(0, 7) <= month) throw new ApiError(409, "Закрыть месяц можно после его окончания по московскому времени.");
        const note = text(payload.note, "Основание закрытия", 4000);
        if (record.policy.profile === "notion5" && (record.revenuePercent === null || !record.revenueEvidence.trim())) throw new ApiError(400, "Для закрытия укажите долю своевременных поступлений и основание расчёта.");
        if (record.policy.draft) throw new ApiError(409, "Сначала подтвердите правила месяца в настройках с указанием основания.");
        const calculation = calculateMonth(record, at);
        if (!calculation.periodEnded || !calculation.totalTasks || calculation.blocks.some(block => block.pending > 0)) throw new ApiError(409, "Для закрытия месяца должны наступить все сроки и быть сформирован календарь задач.");
        const closure = { at, by: context.user.userId, note };
        details = { before: record.closure, after: closure };
        record.closure = closure;
        reason = note;
        break;
      }
      case "reopen": {
        onlyKeys(payload, [...baseKeys, "reason"]);
        if (!record.closure) throw new ApiError(409, "Месяц уже открыт.");
        reason = text(payload.reason, "Причина повторного открытия", 2000);
        details = { before: record.closure, after: null };
        record.closure = null;
        break;
      }
      default: throw new ApiError(400, "Неизвестное действие.");
    }

    await saveMonth(context, record, revision, action, reason, details, allowed);
    return response(await snapshot(context));
  } catch (error) { return errorResponse(error); }
}
