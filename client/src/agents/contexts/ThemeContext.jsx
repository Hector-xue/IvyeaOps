import React, { createContext, useContext, useState, useEffect } from 'react';

import { themeMode } from '../../lib/themes';

const ThemeContext = createContext();

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};

export const ThemeProvider = ({ children }) => {
  // 跟随 ops 主题的明暗。判据来自 lib/themes 的注册表，**不再是
  // `theme !== 'light'`** —— 那个写法把"除了月岩以外都是深色"焊死了，
  // 再加任何一套浅色主题都会被误判成深色。
  const [isDarkMode, setIsDarkMode] = useState(() => {
    try { return themeMode(localStorage.getItem('ivyea-ops.theme')) === 'dark'; }
    catch { return true; }
  });

  // 监听 ops 主题切换,实时更新明暗
  useEffect(() => {
    const onTheme = (e) => {
      const t = (e && e.detail) || localStorage.getItem('ivyea-ops.theme');
      setIsDarkMode(themeMode(t) === 'dark');
    };
    window.addEventListener('ivyea-ops:theme-changed', onTheme);
    return () => window.removeEventListener('ivyea-ops:theme-changed', onTheme);
  }, []);

  // **`.dark` 的写入交给 utils/ivyeaOpsTheme.applyIvyeaOpsTheme 独占。**
  // 这里以前也写一份，两个写入者算出不同结果时（比如新增浅色主题只改了一处）
  // 会出现「HSL 变量已经是浅色、.dark 还挂着」的撕裂，而且看不出是谁写的。
  // 这个 context 现在只**读**明暗，供代码高亮/编辑器主题这类 JS 侧消费。


  const toggleDarkMode = () => {
    setIsDarkMode(prev => !prev);
  };

  const value = {
    isDarkMode,
    toggleDarkMode,
  };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
};