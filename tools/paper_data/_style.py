"""논문 그림 공통 스타일 — 한글 글꼴(Noto Sans CJK), 색, 저장."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

for _f in ('Noto Sans CJK KR', 'Noto Sans CJK JP', 'NanumGothic'):
    if any(_f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams['font.family'] = _f
        break
plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 9, 'axes.grid': True, 'grid.alpha': 0.3,
                     'axes.spines.top': False, 'axes.spines.right': False, 'savefig.bbox': 'tight'})

# 송신(우리) / 수신(차량) / 제3자(원격) — 그림 전체에서 같은 뜻 같은 색
C = {'tx': '#D55E00', 'rx': '#0072B2', 'rx2': '#56B4E9', 'remote': '#009E73', 'other': '#999999', 'ref': '#333333'}
