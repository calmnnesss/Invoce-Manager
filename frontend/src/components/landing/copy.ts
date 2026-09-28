/**
 * 项目介绍页的全部文案（中 / 英）。
 *
 * 只描述当前系统，每条机制说明都对应代码里的实现；改了融合、校验或复核的规则，这里要同步：
 *   识别与状态   backend/app/services/invoice_service.py、qwen_ocr_service.py
 *   融合与校验   backend/app/services/fusion.py（fuse、run_checks、reconcile_amounts）
 *   人工复核     backend/app/services/review.py、backend/app/routers/invoices.py
 * 二维码线路对外按「待上线」表述（代码里有，但还没在真实发票上验证过）。
 *
 * 文案里的 [[界面路径]] 渲染成标签，`按键` 渲染成键帽（见 LandingPage 的 renderInline）。
 */

export type Lang = 'zh' | 'en';

export const REPO_URL = 'https://github.com/calmnnesss/Invoce-Manager';
export const CODE_URL = `${REPO_URL}/blob/master/backend/app/services`;

/** 金额组容差的出厂值（fusion.py 的 AmountRules）。设置页可以改，所以文案里标为「默认」。 */
const TOLERANCE = { add: '0.01', mul: '0.02', rounding: '0.05' };

/** 校验规则的 id，顺序与 fusion.run_checks 一致；diagram.checks 按同样顺序给出说明 */
export const RULE_IDS = [
  'seller_is_entity',
  'amount_consistency',
  'tax_id_format',
  'invoice_number_format',
  'cross_source_mismatch',
] as const;

interface Step {
  title: string;
  body: string;
}

interface Principle {
  title: string;
  body: string;
  /** 「查看代码」链接到的文件，相对 backend/app/services */
  files: string[];
}

export interface Copy {
  meta: { htmlLang: string; title: string };
  nav: { language: string; github: string; openApp: string };
  hero: {
    eyebrow: string;
    title: string;
    openApp: string;
    github: string;
    tryIt: string;
  };
  how: {
    title: string;
    lead: string;
    steps: [Step, Step, Step, Step];
    fieldsSummary: string;
    fieldsHead: { field: string; source: string };
    fields: { field: string; source: string }[];
  };
  diagram: {
    title: string;
    desc: string;
    settings: string;
    settingsNote: string;
    upload: string;
    formats: string;
    ocr: string;
    vlm: string;
    vlmNote: string;
    qr: string;
    parallel: string;
    fuseNote: string;
    confidence: string;
    levels: { certain: string; high: string; low: string; missing: string };
    buyer: string;
    amounts: string;
    checkNote: string;
    checks: [string, string, string, string, string];
    pass: string;
    flagged: [string, string];
    confirmed: string;
    reviewNotes: [string, string];
    confirm: string;
    loop: string;
  };
  principles: {
    title: string;
    lead: string;
    viewCode: string;
    items: [Principle, Principle, Principle, Principle];
    layers: { name: string; rule: string }[];
  };
  tryIt: {
    title: string;
    steps: [string, string, string];
    openApp: string;
    note: string;
  };
  footer: { project: string; stack: string; github: string; openApp: string };
}

const STACK = 'FastAPI · PostgreSQL · React · Ant Design · qwen3.5-ocr · qwen-vl-max';

export const COPY: Record<Lang, Copy> = {
  zh: {
    meta: { htmlLang: 'zh-CN', title: 'Invoice-Manager · 增值税发票识别系统' },
    nav: { language: '切换语言', github: 'GitHub', openApp: '进入系统' },
    hero: {
      eyebrow: '面向企业报销',
      title: '增值税发票识别系统',
      openApp: '进入系统',
      github: 'GitHub',
      tryIt: '用自己的发票试试 ↓',
    },
    how: {
      title: '工作流程',
      lead: '从上传到确认，四步。',
      steps: [
        {
          title: '01 识别',
          body: 'OCR 按固定字段表返回键值对；视觉模型读整张票，购买方从你的主体清单里选。两路并行，互不参考。二维码识别待上线。',
        },
        {
          title: '02 融合',
          body: '每个字段按固定的优先顺序取值，并记录来源和置信度。只有一路读到、或几路不一致，记为「低」。',
        },
        {
          title: '03 校验',
          body: '五项检查。有未通过的检查或低置信字段，发票进入待审核。销售方是你清单里的公司时判为拒绝，销售方两项清空，等人填写。',
        },
        {
          title: '04 人工核对',
          body: '只处理标为「需要复核」的字段，项目名称每张都由人确认。每次修改都按同一套规则重新校验。',
        },
      ],
      fieldsSummary: '每个字段从哪来',
      fieldsHead: { field: '字段', source: '取值来源' },
      fields: [
        { field: '发票代码、号码、开票日期', source: '二维码（待上线）→ 视觉模型 → OCR' },
        { field: '购买方名称、税号', source: '按视觉模型选出的 id 查主体清单，OCR 读数用来核对' },
        { field: '销售方名称、税号', source: 'OCR → 视觉模型' },
        { field: '金额、税率、税额、价税合计', source: '视觉模型优先，四项按两个等式一起判定' },
        { field: '项目名称', source: '仅视觉模型，始终由人确认' },
      ],
    },
    diagram: {
      title: '处理流程',
      desc: '发票上传后由 OCR 与视觉模型并行识别，逐字段融合并标注置信度，经五项校验后，全部通过的直接确认，其余交人工核对；每次修改后重新校验。',
      settings: '设置：购买方主体 · 金额规则',
      settingsNote: '购买方主体与金额规则可在「设置」里修改。',
      upload: '上传',
      formats: 'PDF · JPG · PNG',
      ocr: 'OCR · qwen3.5-ocr',
      vlm: 'VLM · qwen-vl-max',
      vlmNote: '购买方从清单选 id',
      qr: '二维码 · 待上线',
      parallel: '两路并行，互不参考',
      fuseNote: '逐字段选源',
      confidence: '置信度',
      levels: { certain: '确定', high: '高', low: '低', missing: '缺失' },
      buyer: '购买方 · 查表',
      amounts: '金额组 · 两式互证',
      checkNote: '五项检查',
      checks: ['销售方不在主体清单', '金额两式勾稽', '税号位数与校验位', '发票代码、号码位数', '多路结果一致'],
      pass: '全部通过',
      flagged: ['警告', '拒绝'],
      confirmed: '已确认',
      reviewNotes: ['只处理需要复核的字段', '项目名称始终人工确认'],
      confirm: '确认',
      loop: '每次修改后按同一套规则重新校验',
    },
    principles: {
      title: '设计原则',
      lead: '没有哪个字段只听一个模型的。',
      viewCode: '查看代码',
      items: [
        {
          title: '购买方从清单里选',
          body: '视觉模型不抄写购买方，而是从主体清单里选一个 id，名称和税号按清单原样填入，不会出现错字、少位或串行。',
          files: ['prompts.py', 'fusion.py'],
        },
        {
          title: '金额四项互相印证',
          body: '金额 A、税率 r、税额 T、价税合计 S 要同时满足 A + T = S 与 A × r = T，所以四项一起判定，而不是逐项比对。二维码读出或人确认过的值不会被推导改写；容差和标准税率可在设置里调整。',
          files: ['fusion.py'],
        },
        {
          title: '拿不准就标出来',
          body: '每个字段都记录来源（二维码、OCR、视觉模型、查表、计算、人工）和置信度。各路结果存在冲突时，会在核对页高亮提示，由你来兜底确认。',
          files: ['fusion.py'],
        },
        {
          title: '人的判断优先',
          body: '每次修改后，服务端用同一套五项校验重新判定整张发票；改了金额中的一项，整组按同样的三层规则重算。人填写或确认过的值不会被覆盖。已确认的发票再被修改，会退回待审核。',
          files: ['review.py'],
        },
      ],
      layers: [
        { name: '第一层', rule: `两式都成立 → 通过（默认容差：加法 ${TOLERANCE.add} 元，乘法 ${TOLERANCE.mul} 元）` },
        { name: '第二层', rule: `只差一项 → 推导：反推标准税率（对上多个就不猜）、补全缺项；乘法差额不超过 ${TOLERANCE.rounding} 元（默认）按多行明细凑整` },
        { name: '第三层', rule: '其余 → 交人确认' },
      ],
    },
    tryIt: {
      title: '用你自己的发票试试',
      steps: [
        '[[上传发票]] 页内置了两张示例发票，购买方已在默认主体清单里，可以直接使用。想用自己的发票，先在 [[设置 → 购买方主体]] 里加入你公司的名称和税号，购买方只会从这份清单里选。',
        '在 [[上传发票]] 上传 PDF、JPG 或 PNG，单个文件不超过 10 MB，可以一次选多个。PDF 以第一页为准。',
        '在发票列表里打开它，识别完成后进入核对页：`Enter` 确认当前项并跳到下一项，`?` 查看全部快捷键。右上角 [[⋮ → 识别详情]] 列出每个字段的各路读数、来源、置信度和校验结果。',
      ],
      openApp: '进入系统',
      note: '系统配置的是私有 API Key，请按需体验。',
    },
    footer: { project: '个人项目', stack: STACK, github: 'GitHub', openApp: '进入系统' },
  },

  en: {
    meta: { htmlLang: 'en', title: 'Invoice-Manager · Chinese VAT invoice recognition system' },
    nav: { language: 'Language', github: 'GitHub', openApp: 'Open the app' },
    hero: {
      eyebrow: 'For company expense claims',
      title: 'Chinese VAT Invoice Recognition System',
      openApp: 'Open the app',
      github: 'GitHub',
      tryIt: 'Try it with your own invoices ↓',
    },
    how: {
      title: 'How it works',
      lead: 'Four stages from upload to a confirmed invoice.',
      steps: [
        {
          title: '01 Read',
          body: 'OCR returns key–value pairs for a fixed list of fields. The vision model reads the whole invoice and picks the buyer from your entity list. The two run in parallel, blind to each other. QR code reading is coming soon.',
        },
        {
          title: '02 Fuse',
          body: 'Each field takes its value in a fixed priority order and records its source and confidence. One source only, or sources that disagree, means low.',
        },
        {
          title: '03 Check',
          body: 'Five checks. A failed check or any low-confidence field sends the invoice to review. If the seller is one of your own entities, the invoice is rejected and both seller fields are cleared for a person to fill in.',
        },
        {
          title: '04 Review',
          body: 'A person handles only the fields marked for review, and the item name is always one of them. Every edit is re-checked with the same rules.',
        },
      ],
      fieldsSummary: 'Where each field comes from',
      fieldsHead: { field: 'Field', source: 'Source' },
      fields: [
        { field: 'Invoice code, number, date', source: 'QR (coming soon) → VLM → OCR' },
        { field: 'Buyer name, tax ID', source: "Looked up in your entity list by the ID the VLM picked; OCR's reading is the cross-check" },
        { field: 'Seller name, tax ID', source: 'OCR → VLM' },
        { field: 'Amount, tax rate, tax, total', source: 'VLM first; all four are judged together by two equations' },
        { field: 'Item name', source: 'VLM only; always confirmed by a person' },
      ],
    },
    diagram: {
      title: 'Processing pipeline',
      desc: 'After upload, an OCR model and a vision model read the invoice in parallel. The readings are fused field by field with a confidence level and run through five checks: invoices that pass everything are confirmed, the rest go to a person, and every edit is re-checked.',
      settings: 'Settings: buyer entities · amount rules',
      settingsNote: 'Buyer entities and amount rules are edited in Settings.',
      upload: 'Upload',
      formats: 'PDF · JPG · PNG',
      ocr: 'OCR · qwen3.5-ocr',
      vlm: 'VLM · qwen-vl-max',
      vlmNote: 'picks buyer ID from list',
      qr: 'QR code · coming soon',
      parallel: 'run in parallel, blind to each other',
      fuseNote: 'a source per field',
      confidence: 'confidence',
      levels: { certain: 'certain', high: 'high', low: 'low', missing: 'missing' },
      buyer: 'Buyer · list lookup',
      amounts: 'Amounts · two equations',
      checkNote: 'five checks',
      checks: ["Seller isn't on your list", 'Amounts reconcile', 'Tax ID length & check digit', 'Code & number length', 'Sources agree'],
      pass: 'all pass',
      flagged: ['warn', 'rejected'],
      confirmed: 'Confirmed',
      reviewNotes: ['flagged fields only', 'item name: always a person'],
      confirm: 'confirm',
      loop: 're-checked with the same rules after every edit',
    },
    principles: {
      title: 'Design principles',
      lead: "No field is taken on one model's word.",
      viewCode: 'View code',
      items: [
        {
          title: 'The buyer is picked from a list',
          body: "The VLM doesn't transcribe the buyer; it picks an ID from your entity list, and the name and tax ID are filled in exactly as listed, so the field never carries a typo, a missing digit or text from the next line.",
          files: ['prompts.py', 'fusion.py'],
        },
        {
          title: 'Four amounts that check each other',
          body: 'Amount A, rate r, tax T and total S must satisfy A + T = S and A × r = T, so the four are judged together, not one at a time. Values read from a QR code or confirmed by a person are never rewritten, and the tolerances and standard rates are editable in Settings.',
          files: ['fusion.py'],
        },
        {
          title: "Flag it, don't guess",
          body: 'Every field records its source (QR, OCR, VLM, lookup, computed, manual) and a confidence level. When the sources conflict, the field is highlighted on the review page for you to settle.',
          files: ['fusion.py'],
        },
        {
          title: 'People have the last word',
          body: 'After every edit, the server re-runs the same five checks on the whole invoice, and changing one amount re-judges the group with the same three layers. Values a person entered or confirmed are never overwritten. Editing a confirmed invoice sends it back to review.',
          files: ['review.py'],
        },
      ],
      layers: [
        { name: 'Layer 1', rule: `Both hold → pass (default tolerance: ¥${TOLERANCE.add} for addition, ¥${TOLERANCE.mul} for multiplication)` },
        { name: 'Layer 2', rule: `One value off or missing → derive it: snap to a standard rate (never guessing between two), fill the gap; a multiplication gap up to ¥${TOLERANCE.rounding} (default) counts as rounding across line items` },
        { name: 'Layer 3', rule: 'Anything else → a person' },
      ],
    },
    tryIt: {
      title: 'Try it with your own invoices',
      steps: [
        "The [[上传发票]] (Upload) page has two sample invoices whose buyer is already in the default entity list, so you can use them right away. To use your own invoices, first add your company's name and tax ID in [[设置 → 购买方主体]] (Settings → Buyer entities); the buyer is only ever picked from this list.",
        'On [[上传发票]] (Upload), add PDF, JPG or PNG files, up to 10 MB each, several at a time. For PDFs, the first page is used.',
        "Open the invoice from the list. Once recognition finishes you're on the review page: `Enter` confirms a field and moves to the next, and `?` shows every shortcut. [[⋮ → 识别详情]] (Recognition details) at the top right lists each source's reading, the chosen source, confidence and every check's result.",
      ],
      openApp: 'Open the app',
      note: 'The demo runs on a private API key, so please use it in moderation.',
    },
    footer: { project: 'A personal project', stack: STACK, github: 'GitHub', openApp: 'Open the app' },
  },
};
