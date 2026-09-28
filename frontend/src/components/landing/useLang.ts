import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { COPY, type Lang } from './copy';

const STORAGE_KEY = 'im.lang';

/**
 * 首次打开时的语言：链接里的 ?lang= 优先（方便直接发英文链接），其次是上次的选择，
 * 再次是浏览器语言（zh 开头用中文，其余用英文），都没有就用中文。
 */
export function resolveLang(search: string, stored: string | null, browser: string | undefined): Lang {
  const query = new URLSearchParams(search).get('lang');
  if (query === 'zh' || query === 'en') return query;
  if (stored === 'zh' || stored === 'en') return stored;
  if (browser) return browser.toLowerCase().startsWith('zh') ? 'zh' : 'en';
  return 'zh';
}

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // 隐私模式等情况下读不到，按没选过处理
  }
}

/** 介绍页的当前语言。同步 <html lang> 与标题，离开页面时还原成应用自己的。 */
export function useLang(): [Lang, (lang: Lang) => void] {
  const [params, setParams] = useSearchParams();
  const [lang, setLangState] = useState<Lang>(() =>
    resolveLang(window.location.search, readStored(), window.navigator.language));

  // 先于下面的 effect 执行：记下应用原来的标题与语言，卸载时还原
  useEffect(() => {
    const prevTitle = document.title;
    const prevLang = document.documentElement.lang;
    return () => {
      document.title = prevTitle;
      document.documentElement.lang = prevLang;
    };
  }, []);

  useEffect(() => {
    document.documentElement.lang = COPY[lang].meta.htmlLang;
    document.title = COPY[lang].meta.title;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 存不了只影响下次打开时的默认语言
    }
    // 链接里带了 ?lang= 的话一起改掉，否则刷新会回到链接指定的语言
    if (params.has('lang')) setParams({ lang: next }, { replace: true });
  }, [params, setParams]);

  return [lang, setLang];
}
