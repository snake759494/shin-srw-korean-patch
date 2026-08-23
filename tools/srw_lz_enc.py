# -*- coding: utf-8 -*-
"""SRWCB 그래픽 압축 인코더 (srw_lz.decompress 의 역).

디코더가 플래그 바이트를 '가져오는 순간'에 그 바이트를 스트림에 끼워 넣어야 하므로
비트 라이터가 디코더의 카운터 동작을 그대로 흉내낸다.

주의: 메인 루프에서 읽는 플래그 바이트가 0xFF 면 디코더는 '리터럴 8개 고속 경로'로
빠진다. 8비트가 전부 리터럴일 때만 0xFF 가 되므로 결과는 같지만, 인코딩 후 반드시
디코더로 되돌려 원본과 일치하는지 검증한다.
"""


class _W:
    def __init__(self):
        self.out = bytearray()
        self.counter = 0x80
        self.fpos = -1
        self.fbit = 0

    def bit(self, b):
        self.counter <<= 1
        if self.counter & 0x100:
            self.counter = 1
            self.fpos = len(self.out)
            self.out.append(0)
            self.fbit = 0
        if b:
            self.out[self.fpos] |= 1 << (7 - self.fbit)
        self.fbit += 1

    def byte(self, v):
        self.out.append(v & 0xFF)


def _cost(ln, d):
    """이 (길이,거리) 매치를 내보낼 때의 비트 수. 불가능하면 None."""
    if ln < 2 or d < 1 or d > 8192:
        return None
    if ln <= 5 and d <= 256:
        return 11
    if 3 <= ln <= 9:
        return 18
    if ln >= 10:
        return 26
    return None


def compress(src: bytes, level: int = 3) -> bytes:
    n = len(src)
    w = _W()
    heads = {}                 # 3바이트 프리픽스 -> 최근 위치들
    i = 0
    CHAIN = 64 * level

    def find(i):
        bl = 0; bd = 0
        if i + 3 <= n:
            key = src[i:i + 3]
            for p in reversed(heads.get(key, ())[-CHAIN:]):
                dist = i - p
                if dist > 8192:
                    break
                ln = 3
                lim = min(256, n - i)
                while ln < lim and src[p + ln] == src[i + ln]:
                    ln += 1
                if ln > bl or (ln == bl and dist < bd):
                    bl = ln; bd = dist
                    if ln >= 256:
                        break
        if bl < 3 and i + 2 <= n:
            j = src.rfind(src[i:i + 2], max(0, i - 256), i)
            if j >= 0:
                bl = 2; bd = i - j
        return bl, bd

    pend = None                # 미리 계산해 둔 다음 위치 매치
    while i < n:
        best_len, best_dist = pend if pend is not None else find(i)
        pend = None
        # 지연 평가: 지금 매치보다 한 칸 뒤 매치가 '바이트당 비트'가 나으면 리터럴로 미룬다
        if best_len >= 2 and i + 1 < n:
            c0 = _cost(best_len, best_dist)
            nl, nd = find(i + 1)
            pend = (nl, nd)
            c1 = _cost(nl, nd)
            if c0 and c1 and (9 + c1) / (1 + nl) < c0 / best_len:
                best_len = 0
        else:
            pend = None

        emit_lit = True
        if best_len >= 2:
            d = best_dist
            if best_len <= 5 and d <= 256:                     # 숏 매치 11비트
                emit_lit = False
                w.bit(0); w.bit(0)
                code = best_len - 2
                w.bit((code >> 1) & 1); w.bit(code & 1)
                w.byte((0x100 - d) & 0xFF)
            elif 3 <= best_len <= 9 and d <= 8192:             # 롱 매치 18비트
                emit_lit = False
                w.bit(0); w.bit(1)
                word = (((0x2000 - d) & 0x1FFF) << 3) | (best_len - 2)
                w.byte(word >> 8); w.byte(word & 0xFF)
            elif best_len >= 10 and d <= 8192:                 # 롱 확장 26비트
                emit_lit = False
                ln = min(best_len, 256)
                w.bit(0); w.bit(1)
                word = ((0x2000 - d) & 0x1FFF) << 3
                w.byte(word >> 8); w.byte(word & 0xFF)
                w.byte(ln - 1)
                best_len = ln
        if emit_lit:
            w.bit(1); w.byte(src[i])
            best_len = 1
        else:
            pend = None        # 매치를 냈으면 미리 계산한 다음 위치는 무효
        for k in range(best_len):
            p = i + k
            if p + 3 <= n:
                heads.setdefault(src[p:p + 3], []).append(p)
        i += best_len
    # 종료: 롱 매치 형식 + 길이바이트 0
    w.bit(0); w.bit(1)
    w.byte(0xFF); w.byte(0xF8)      # offset=-1, len3=0
    w.byte(0)
    return bytes(w.out)


if __name__ == "__main__":
    import sys, os, random
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from srw_lz_fast import decompress
    random.seed(1)
    for trial in range(6):
        data = bytes(random.choice(b"\x00\x0c\xcc\xff\x12\x34") for _ in range(5000))
        c = compress(data)
        d, used = decompress(c, 0)
        assert d == data and used == len(c), (trial, len(d), len(data), used, len(c))
        print(f"  trial{trial}: {len(data)} -> {len(c)} ({len(c)/len(data):.2f}) OK")
