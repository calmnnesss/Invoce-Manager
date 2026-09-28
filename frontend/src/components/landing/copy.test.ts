import { describe, expect, it } from 'vitest';
import { COPY, type Lang } from './copy';
import { resolveLang } from './useLang';

/** 收集所有文案叶子，带上路径，便于定位 */
function leaves(value: unknown, path: string, out: [string, string][]): [string, string][] {
  if (typeof value === 'string') out.push([path, value]);
  else if (Array.isArray(value)) value.forEach((v, i) => leaves(v, `${path}[${i}]`, out));
  else if (value && typeof value === 'object') {
    Object.entries(value).forEach(([k, v]) => leaves(v, `${path}.${k}`, out));
  }
  return out;
}

describe('介绍页文案', () => {
  it.each(['zh', 'en'] as Lang[])('%s：切换到这个语言时没有空白文案', (lang) => {
    const empty = leaves(COPY[lang], lang, []).filter(([, text]) => !text.trim());
    expect(empty).toEqual([]);
  });

  it.each(['zh', 'en'] as Lang[])('%s：界面路径 [[…]] 与按键 `…` 标记成对，不会把标记符号原样显示出来', (lang) => {
    const broken = leaves(COPY[lang], lang, []).filter(([, text]) => {
      const rest = text.replace(/\[\[.+?\]\]/g, '').replace(/`.+?`/g, '');
      return /\[\[|\]\]|`/.test(rest);
    });
    expect(broken).toEqual([]);
  });
});

describe('介绍页语言选择', () => {
  it('链接里的 ?lang= 优先于上次的选择和浏览器语言', () => {
    expect(resolveLang('?lang=en', 'zh', 'zh-CN')).toBe('en');
    expect(resolveLang('?lang=zh', 'en', 'en-US')).toBe('zh');
  });

  it('没有指定时用上次的选择，其次看浏览器语言', () => {
    expect(resolveLang('', 'en', 'zh-CN')).toBe('en');
    expect(resolveLang('', null, 'zh-TW')).toBe('zh');
    expect(resolveLang('', null, 'en-GB')).toBe('en');
  });

  it('无效的参数和存储值被忽略，什么都没有时用中文', () => {
    expect(resolveLang('?lang=fr', 'de', undefined)).toBe('zh');
  });
});
