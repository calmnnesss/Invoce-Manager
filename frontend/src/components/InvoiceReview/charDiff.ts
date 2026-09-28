export interface DiffSegment {
  text: string;
  /** 这段字符不在两者的最长公共子序列里，即与对方不同的部分 */
  changed: boolean;
}

/**
 * 相似度（公共子序列长度 / 较长串长度）低于它就不高亮：两个值根本不是同一个东西的
 * 不同读法时（例如识别成了另一家公司），把公共的零星几个字标出来只会误导。
 */
const MIN_SIMILARITY = 0.6;

/**
 * 把 a 切成「与 b 相同 / 不同」的若干段，用来在候选值里高亮差异（广州**晶东** vs 广州**品乐**）。
 * 基于最长公共子序列，适用于名称、税号这类短字符串；只在两者是「同一个值读错了几个字」时高亮。
 */
export function diffAgainst(a: string, b: string): DiffSegment[] {
  const x = Array.from(a);
  const y = Array.from(b);
  const n = x.length;
  const m = y.length;

  // lcs[i][j] = x[i..] 与 y[j..] 的最长公共子序列长度
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = x[i] === y[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  if (n === 0 || lcs[0][0] / Math.max(n, m) < MIN_SIMILARITY) {
    return n ? [{ text: a, changed: false }] : [];
  }

  const segments: DiffSegment[] = [];
  const push = (ch: string, changed: boolean) => {
    const last = segments[segments.length - 1];
    if (last && last.changed === changed) last.text += ch;
    else segments.push({ text: ch, changed });
  };

  let i = 0;
  let j = 0;
  while (i < n) {
    if (j < m && x[i] === y[j]) {
      push(x[i], false);
      i++;
      j++;
    } else if (j < m && lcs[i][j + 1] > lcs[i + 1][j]) {
      j++;
    } else {
      push(x[i], true);
      i++;
    }
  }
  return segments;
}
