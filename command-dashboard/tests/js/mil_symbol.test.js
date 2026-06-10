/**
 * mil_symbol.test.js — P2-05(c)：cot_type → affiliation / SIDC 純轉換（#110）
 *
 * 鎖住的不變式：
 *   - affiliationFromCot：CoT 第 2 段 → 4 態（f/a→friendly, h/s/j/k→hostile, n→neutral, 其餘→unknown）
 *   - cotToSidc：atom → 'S{aff}{dim}P------…'（milsymbol 可解的 2525C SIDC）；非 atom → null
 *   - 產出的 SIDC 能讓 milsymbol isValid（與 vendored lib 對接）
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { affiliationFromCot, cotToSidc, affiliationToCotType } from '../../static/js/map/mil_symbol.js';

// 載入 vendored milsymbol（UMD）→ 取 CJS 出口，驗 SIDC 真能渲染
const _here = dirname(fileURLToPath(import.meta.url));
function loadMilsymbol() {
  const code = readFileSync(resolve(_here, '../../static/lib/milsymbol.js'), 'utf8');
  const mod = { exports: {} };
  new Function('module', 'exports', code)(mod, mod.exports);
  return mod.exports;
}

describe('affiliationFromCot', () => {
  it('friendly：f / a（assumed friend）', () => {
    expect(affiliationFromCot('a-f-G-U-C')).toBe('friendly');
    expect(affiliationFromCot('a-a-G')).toBe('friendly');
  });
  it('hostile：h / s（suspect）/ j(joker) / k(faker)', () => {
    for (const c of ['a-h-G', 'a-s-A', 'a-j-G', 'a-k-G']) expect(affiliationFromCot(c)).toBe('hostile');
  });
  it('neutral：n', () => {
    expect(affiliationFromCot('a-n-G')).toBe('neutral');
  });
  it('unknown：u / p（pending）/ 未知字母', () => {
    expect(affiliationFromCot('a-u-A')).toBe('unknown');
    expect(affiliationFromCot('a-p-G')).toBe('unknown');
    expect(affiliationFromCot('a-z-G')).toBe('unknown');
  });
  it('非 atom（b-*）/ 非字串 → unknown', () => {
    expect(affiliationFromCot('b-m-r')).toBe('unknown');
    expect(affiliationFromCot(null)).toBe('unknown');
    expect(affiliationFromCot(undefined)).toBe('unknown');
  });
  it('大小寫不敏感', () => {
    expect(affiliationFromCot('A-F-G')).toBe('friendly');
  });
});

describe('cotToSidc', () => {
  it('atom → S{aff}{dim}P + generic function', () => {
    expect(cotToSidc('a-f-G-U-C')).toBe('SFGP-----------');
    expect(cotToSidc('a-h-G')).toBe('SHGP-----------');
    expect(cotToSidc('a-n-G')).toBe('SNGP-----------');
  });
  it('dimension 對映：A 空中 / S 海面 / U 水下；未知 → 地面 G', () => {
    expect(cotToSidc('a-f-A')).toBe('SFAP-----------');
    expect(cotToSidc('a-f-S')).toBe('SFSP-----------');
    expect(cotToSidc('a-h-U')).toBe('SHUP-----------');
    expect(cotToSidc('a-f-X')).toBe('SFGP-----------'); // X 未對映 → 預設地面
  });
  it('非 atom（route/polygon）→ null（走既有渲染，不套 2525 unit 框）', () => {
    expect(cotToSidc('b-m-r')).toBe(null);
    expect(cotToSidc('u-d-f')).toBe(null);
    expect(cotToSidc(null)).toBe(null);
  });
});

describe('affiliationToCotType（P2-30 part 3：建敵情標記，affiliationFromCot 之逆）', () => {
  it('四態 → generic 地面 atom', () => {
    expect(affiliationToCotType('friendly')).toBe('a-f-G');
    expect(affiliationToCotType('hostile')).toBe('a-h-G');
    expect(affiliationToCotType('neutral')).toBe('a-n-G');
    expect(affiliationToCotType('unknown')).toBe('a-u-G');
  });
  it('未知/缺省 → 不明（fail-safe，不誤標友軍）', () => {
    expect(affiliationToCotType('garbage')).toBe('a-u-G');
    expect(affiliationToCotType(undefined)).toBe('a-u-G');
  });
  it('與 affiliationFromCot 互逆（round-trip）', () => {
    for (const aff of ['friendly', 'hostile', 'neutral', 'unknown']) {
      expect(affiliationFromCot(affiliationToCotType(aff))).toBe(aff);
    }
  });
  it('產出能被 cotToSidc 渲染（hostile → SIDC 位2=H，2525 紅框）', () => {
    expect(cotToSidc(affiliationToCotType('hostile'))[1]).toBe('H');
  });
});

describe('cotToSidc ↔ milsymbol 對接', () => {
  const ms = loadMilsymbol();
  it('產出的 SIDC 能讓 milsymbol isValid + asSVG', () => {
    for (const cot of ['a-f-G-U-C', 'a-h-G', 'a-n-G', 'a-u-A']) {
      const sym = new ms.Symbol(cotToSidc(cot), { size: 24 });
      expect(sym.isValid(false)).toBe(true);
      expect(sym.asSVG().startsWith('<svg')).toBe(true);
    }
  });
});
