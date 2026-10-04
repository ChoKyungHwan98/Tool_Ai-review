/* 개요 · 주제별 보기 · 칭찬 분석 화면. 한 화면이 질문 하나에 답한다.
   개요(#ovBody): 무엇을 얼마나 봤고 전체 반응은 어떤가 — 같은 모양 카드 5장, Steam 전체 리뷰의 날짜별 추천·비추천 차트, 글에 담긴 반응.
   주제별 보기(#tpBody): 주제마다 칭찬과 불만이 얼마나 나왔나 — 주제 줄, IPA 매트릭스, 선택한 주제의 근거.
   칭찬 분석(#chBody): 무엇을 칭찬하고 무엇을 즐겼나 — 칭찬이 많은 주제, 즐긴 것, 즐긴 것을 말한 리뷰.
   플레이 시간별 비추천율(#rdCohortChart)은 불만 분석 화면에 놓이고 여기서 그린다.
   배치는 LUNO 대학 대시보드(dashboard-university)의 줄 구성을 따른다.
   결론 문장은 쓰지 않는다. 판단은 사람이 한다.
   모든 막대와 문장은 현재 게임의 evidence 집계로만 그린다. 설계 규칙은 AGENTS.md 참고. */
window.ReviewDashboard = (() => {
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = x => x == null ? '—' : Number(x).toLocaleString('ko-KR');
  const pct = x => x == null ? '—' : `${Number(x).toFixed(1)}%`;
  const clamp = x => Math.max(0, Math.min(100, Number(x) || 0));
  // 리뷰를 어떻게 모았는지. 설명 문장이 실제 수집 방법과 다르면 안 된다.
  const scope = d => {
    const p = d?.sample_design_full?.params || {};
    if (p.sort === 'random') {
      return { random: true, bias: '리뷰를 쓴 사람만 담긴다는 치우침',
        text: (p.random_pool?.complete ? '수집 범위의 리뷰를 끝까지 훑고 그중에서 무작위로 뽑은 리뷰입니다.' : '무작위로 뽑았지만 수집 범위를 끝까지 훑지는 못한 리뷰입니다.')
          + ' 리뷰는 자발적으로 작성한 의견이어서, 리뷰를 쓰지 않은 유저의 생각까지 대표하지는 않습니다.' };
    }
    const label = p.sort === 'helpful' ? '공감순' : '최신순';
    return { random: false, bias: `${label} 수집의 치우침`,
      text: `${label}으로 수집한 리뷰입니다. 추천·비추천 비율을 맞춰도 전체 유저나 전체 기간의 무작위 표본이 되지는 않습니다. 리뷰는 자발적으로 작성한 의견입니다.` };
  };
  // 한 리뷰가 같은 주제에 칭찬과 불만을 모두 달 수 있으므로 감성 언급 수를 분모로 쓴다.
  const themeNegShare = t => t.pos + t.neg ? t.neg / (t.pos + t.neg) : 0;
  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    report: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5M4 19h16"/>',
    review: '<path d="M20 15a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    pie: '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
    trend: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    spark: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 16v4M17 18h4"/>',
    matrix: '<path d="M4 4v16h16"/><circle cx="10" cy="13" r="2"/><circle cx="16" cy="8" r="2.5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    smile: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 10h.01M15 10h.01"/>',
    up: '<path d="M7 11v9H4v-9zM7 11l4-7a2 2 0 0 1 2 2v4h5a2 2 0 0 1 2 2.3l-1 6A2 2 0 0 1 17 20H7"/>',
    down: '<path d="M17 13V4h3v9zM17 13l-4 7a2 2 0 0 1-2-2v-4H6a2 2 0 0 1-2-2.3l1-6A2 2 0 0 1 7 4h10"/>',
    scale: '<path d="M12 4v16M6 20h12M5 8h14M5 8l-2.5 6a3 3 0 0 0 5 0zM19 8l-2.5 6a3 3 0 0 0 5 0z"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.3L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.3L20 16M20 20v-4h-4"/>',
    bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>'
  };
  const icon = name => `<svg class="rd-icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
  let data, evidence, appId, root, selected, trendUnit = 'recent', histogram = null, resizeBound = false;
  let dialogTheme, dialogSentiment, dialogPage, request, restoreFocus, sizeObserver;
  let trendCharts = [], trendStamp = '';

  // 칭찬이 더 많은 주제는 칭찬순, 불만이 더 많은 주제는 불만순. 차트에서 진하게 칠하는 두 주제를 고르는 기준이다.
  function groups() {
    const praised = evidence.themes.filter(t => t.pos >= t.neg && t.mentions > 0)
      .sort((a,b) => b.pos - a.pos || a.neg - b.neg || a.name.localeCompare(b.name));
    const disliked = evidence.themes.filter(t => t.neg > t.pos)
      .sort((a,b) => b.neg - a.neg || a.pos - b.pos || a.name.localeCompare(b.name));
    return {praised, disliked};
  }
  function strength() { return groups().praised.find(t => t.pos > 0); }
  function concern() { return groups().disliked[0]; }
  // 불만이 앞서는 주제가 없을 때 보조로 보여줄 가장 많은 불만
  function loudest() { return [...evidence.themes].filter(t => t.neg > 0).sort((a,b) => b.neg - a.neg)[0]; }
  function themeByName(name) { return evidence.themes.find(t => t.name === name); }
  function role(t) {
    if (!t) return null;
    if (t.name === concern()?.name) return 'concern';
    if (t.name === strength()?.name) return 'strength';
    if (!concern() && t.name === loudest()?.name) return 'watch';
    return t.neg > t.pos ? 'disliked' : 'praised';
  }

  function updateURL() {
    const url = new URL(location.href);
    url.searchParams.set('topic', selected);
    url.searchParams.delete('topic_sort');
    history.replaceState(null, '', url);
  }

  function render(V, id) {
    request?.abort();
    destroyTrend(); histogram = null;
    data = V; evidence = V.evidence; appId = id;
    root = document.getElementById('ovBody');
    const topicRoot = document.getElementById('tpBody'), characterRoot = document.getElementById('chBody');
    const roots = [root, topicRoot, characterRoot].filter(Boolean);
    roots.forEach(el => { el.className = 'rd'; });
    document.getElementById('rdPageActions').innerHTML = `<button class="rd-btn primary" type="button" onclick="ReviewDashboard.report()">${icon('report')}한 장 보고서</button><button class="rd-btn" type="button" onclick="startNewAnalysis(${Number(id)},null,null,true)">${icon('plus')}추가 수집</button><details class="rd-export"><summary class="rd-btn">${icon('download')}내보내기</summary><div class="rd-export-menu"><a href="/api/reviews/download?app_id=${Number(id)}" download>리뷰 원문</a><a href="/api/analysis/download?app_id=${Number(id)}" download>분석 결과</a></div></details>`;
    document.getElementById('overviewTitle').textContent = `${V.game?.name || '게임'} 리뷰 분석`;
    if (!evidence) {
      document.getElementById('ovCoverage').textContent = '';
      roots.forEach(el => { el.innerHTML = '<div class="rd-pane rd-empty">이 분석에는 원문 연결 데이터가 없습니다. 추가 분석 후 확인할 수 있습니다.</div>'; });
      return;
    }
    const counts = evidence.counts;
    const language = ({koreana:'한국어',english:'영어',all:'모든 언어',japanese:'일본어',schinese:'중국어 간체',tchinese:'중국어 번체',unknown:'언어 정보 없음'})[evidence.language] || evidence.language;
    const period = evidence.period ? `${evidence.period.start.replaceAll('-','.')} – ${evidence.period.end.replaceAll('-','.')}` : '기간 정보 없음';
    document.getElementById('ovCoverage').textContent = `${language} Steam 리뷰 · ${period}`;

    const params = new URLSearchParams(location.search);
    const topics = [...evidence.themes].filter(t => t.mentions > 0).sort((a,b) => b.mentions - a.mentions);
    selected = themeByName(params.get('topic'))?.name || topics[0]?.name || evidence.themes[0]?.name;
    const of = (a, b) => b ? a / b * 100 : 0;
    // 개요 윗줄 카드: 아이콘 · 이름 · 값 · 덧붙임 한 가지 모양만 쓴다. 주제 카드는 누르면 주제별 보기로 간다.
    const stat = (tone, iconName, name, value, unit, note) => `<div class="rd-kpi ${tone}">${icon(iconName)}<div class="rd-kpi-name">${name}</div><div class="rd-kpi-value"><b>${value}</b>${unit}</div><small>${note}</small></div>`;
    const topicStat = (tone, iconName, name, t) => `<button type="button" class="rd-kpi ${tone}" data-action="open-topic" data-theme="${esc(t.name)}">${icon(iconName)}<span class="rd-kpi-name">${name}</span><span class="rd-kpi-value"><b>${esc(t.name)}</b></span><small>${num(t.mentions)}건 · 칭찬 ${num(t.pos)} · 불만 ${num(t.neg)}</small></button>`;
    // 비교 기준: Steam 전체의 비추천 비율. 수집한 리뷰의 비율이 실제와 얼마나 다른지 보게 한다.
    const pop = V.sample_design_full?.population || {};
    const popNeg = pop.total ? pop.negative / pop.total * 100 : null;
    const negRate = evidence.sample_negative_rate;
    const mostNeg = [...topics].sort((a,b) => b.neg - a.neg)[0];
    const sumPos = topics.reduce((a, t) => a + t.pos, 0), sumNeg = topics.reduce((a, t) => a + t.neg, 0);

    /* ---- 개요 ---- */
    root.innerHTML = `
      <div class="rd-kpis is-five">
        ${stat('is-accent', 'review', '수집한 리뷰', num(counts.collected), '건', `AI 분석 ${num(counts.analyzed)}건`)}
        ${stat('', 'up', '추천', negRate == null ? '—' : (100 - negRate).toFixed(1), '%', `${num(counts.collected - counts.negative)}건`)}
        ${stat('is-neg', 'down', '비추천', negRate == null ? '—' : Number(negRate).toFixed(1), '%', `${num(counts.negative)}건${popNeg == null ? '' : ` · Steam 전체 ${popNeg.toFixed(1)}%`}`)}
        ${topics[0] ? topicStat('', 'trend', '가장 많이 언급된 주제', topics[0]) : ''}
        ${mostNeg?.neg ? topicStat('is-neg', 'scale', '불만이 가장 많은 주제', mostNeg) : ''}
      </div>
      <div class="rd-grid">
        <section class="rd-pane rd-pane-trend" aria-labelledby="rdTrendTitle">
          <header class="rd-pane-head"><div><h2 id="rdTrendTitle">날짜별 리뷰 수와 비추천 비율</h2><p id="rdTrendNote">Steam 전체 리뷰 · 모든 언어</p></div>
            <div class="rd-pane-tools"><div class="rd-seg" aria-label="기간"><button data-action="trend" data-unit="recent">최근 30일</button><button data-action="trend" data-unit="all">전체 기간</button></div><button class="rd-btn" type="button" data-action="trend-refresh">${icon('refresh')}최신화</button></div></header>
          <div class="rd-pane-body"><div class="rd-strip" id="rdTrendStrip"></div><div id="rdTrendChart"></div></div>
          <p class="rd-note">우리가 수집한 리뷰가 아니라 Steam에 올라온 전체 리뷰(모든 언어)를 날짜별로 센 숫자입니다. 파란 실선은 그날 리뷰 수(왼쪽 눈금), 보라 점선은 비추천 비율(오른쪽 눈금)입니다. 하루 리뷰가 적으면 비추천 몇 건에도 비율이 크게 흔들리므로, 최근 30일 보기의 점선은 그날까지 7일을 합쳐 계산합니다. 그날 하루의 값은 선에 마우스를 올리면 보입니다. 회색 세로선은 개발사가 Steam에 공지를 올린 날입니다. 공지 때문에 리뷰가 늘었다는 뜻은 아닙니다.</p>
        </section>
        <section class="rd-pane rd-pane-mood" aria-labelledby="rdMoodTitle">
          <header class="rd-pane-head"><div><h2 id="rdMoodTitle">글에 담긴 반응</h2><p>AI가 분석한 리뷰 ${num(counts.analyzed)}건</p></div>${icon('pie')}</header>
          <div class="rd-pane-body is-center" id="rdMoodChart"></div>
          <p class="rd-note">Steam의 추천 여부와 별개로, AI가 글 내용을 읽고 긍정 · 혼합 · 부정으로 나눈 결과입니다.${(() => { const up = evidence.counts?.vote_mood?.up; if (!up) return ''; const all = up.P + up.M + up.N + up.U;
            return ` 추천한 글 ${num(all)}건 중 ${num(up.M + up.N)}건은 아쉬운 점도 함께 썼습니다. 그래서 추천 비율보다 긍정 비율이 낮습니다.`; })()}</p>
        </section>
      </div>`;
    // 제목 옆 작은 게임 그림
    document.querySelectorAll('.page .rd-page-header').forEach(header => {
      header.querySelector('.rd-thumb')?.remove();
      if (V.game?.header_image) header.insertAdjacentHTML('afterbegin', `<img class="rd-thumb" src="${esc(V.game.header_image)}" alt="" onerror="this.remove()">`);
    });

    /* ---- 주제별 보기 ---- */
    const negLed = topics.filter(t => t.neg > t.pos);
    const topPos = [...topics].sort((a,b) => b.pos - a.pos)[0], topFun = (evidence.fun || [])[0];
    // 화면마다 맨 위 숫자 카드 줄: 이 줄만 보고 그 화면에 무엇이 있는지 알 수 있게 한다
    if (topicRoot) topicRoot.innerHTML = `
      <div class="rd-kpis">
        ${stat('is-accent', 'matrix', 'AI가 찾은 주제', num(topics.length), '개', counts.themed != null ? `주제가 붙은 리뷰 ${num(counts.themed)}건에서` : `AI 분석 ${num(counts.analyzed)}건에서`)}
        ${stat('', 'up', '칭찬 언급', num(sumPos), '건', `전체 언급의 ${pct(of(sumPos, sumPos + sumNeg))}`)}
        ${stat('is-neg', 'down', '불만 언급', num(sumNeg), '건', `전체 언급의 ${pct(of(sumNeg, sumPos + sumNeg))}`)}
        ${stat('is-neg', 'scale', '불만이 더 많은 주제', num(negLed.length), '개', negLed.length ? esc(negLed.slice(0, 3).map(t => t.name).join(' · ')) + (negLed.length > 3 ? ` 외 ${negLed.length - 3}개` : '') :'모든 주제에서 칭찬이 더 많음')}
      </div>
      <div class="rd-grid">
        <section class="rd-pane rd-pane-chart" aria-labelledby="rdTopicsTitle">
          <header class="rd-pane-head">
            <div><h2 id="rdTopicsTitle">주제별 칭찬과 불만</h2><p>언급이 많은 순서 · 줄을 누르면 아래 근거가 바뀝니다</p></div>
            <button class="rd-text-btn" data-action="method">${icon('info')}분석 기준</button>
          </header>
          <div class="rd-pane-body">
            <div class="rd-legend"><span><i class="pos"></i>칭찬</span><span><i class="neg"></i>불만</span></div>
            <div id="rdTopicChart"></div>
          </div>
        </section>
        <section class="rd-pane rd-pane-map" aria-labelledby="rdMapTitle">
          <header class="rd-pane-head"><div><h2 id="rdMapTitle">IPA 매트릭스</h2><p>오른쪽일수록 많이 언급 · 위일수록 불만 비율이 높음</p></div>${icon('matrix')}</header>
          <div class="rd-pane-body" id="rdMapChart"></div>
          <p class="rd-note">원을 누르면 아래 근거가 그 주제로 바뀝니다. 세로 점선은 언급 수 중앙값, 가로 점선은 불만 50%입니다. 원 크기도 언급 수입니다.</p>
        </section>
        <section class="rd-pane rd-focus" id="rdDetail" aria-live="polite" aria-label="선택한 주제의 근거"></section>
      </div>
      <dialog class="rd-dialog rd-method-dialog" id="rdMethodDialog" aria-labelledby="rdMethodTitle"><div class="rd-dialog-head"><div><h2 id="rdMethodTitle">분석 기준</h2><p>${num(counts.collected)}건 수집 · ${num(counts.analyzed)}건 AI 분석</p></div><button class="rd-btn" data-action="close-method" aria-label="분석 기준 닫기">닫기 ×</button></div><div class="rd-method-grid">
        <p><b>주제별 칭찬과 불만</b><br>한 줄이 주제 하나입니다. 막대 길이는 그 주제를 칭찬(파랑)하거나 불만(보라)으로 언급한 AI 분석 리뷰 수이고, 언급이 많은 주제부터 위에 놓습니다. 오른쪽 숫자는 언급한 리뷰 수와 칭찬·불만 언급 중 불만의 몫입니다.</p>
        <p><b>"불확실" 표시</b><br>언급이 적으면 리뷰 몇 건에 따라 불만 비율이 50%를 넘기도 하고 못 넘기도 합니다. 이 건수에서 우연으로 흔들릴 수 있는 범위(90%, 주제 줄에 마우스를 올리면 보임)가 50%를 걸치면, 칭찬이 많은지 불만이 많은지 말할 수 없어 "불확실"로 적고 강조하지 않습니다. 건수가 적어 생기는 흔들림만 잰 것이고, 전체 유저의 비율에 대한 신뢰구간이 아닙니다. ${scope(V).bias}과 AI 분류 오류는 이 범위에 들어 있지 않습니다.</p>
        <p><b>IPA 매트릭스</b><br>가로축은 주제를 언급한 리뷰 수, 세로축은 칭찬·불만 언급 중 불만의 몫입니다(중요도–성과 분석, IPA). 한 리뷰에 같은 주제의 칭찬과 불만이 함께 있으면 가로축에는 한 번, 세로축에는 각각 한 번씩 셉니다. 원 크기도 언급 리뷰 수입니다. 세로 점선은 전체 주제의 언급 수 중앙값, 가로 점선은 불만 50%입니다. 언급 수가 네 배 이상 차이 나면 가로축을 로그 눈금으로 그립니다.</p>
        <p><b>진하게 칠한 두 주제</b><br>칭찬이 더 많은 주제 중 칭찬 리뷰가 가장 많은 주제(파랑)와 불만이 더 많은 주제 중 불만 리뷰가 가장 많은 주제(보라)입니다. 살펴보기 시작할 곳을 표시한 것이고, 무엇을 고칠지는 원문을 읽고 사람이 판단합니다.</p>
        <p><b>날짜별 추천 · 비추천</b><br>개요의 차트는 우리가 수집한 리뷰가 아니라 Steam에 올라온 전체 리뷰(모든 언어)를 날짜별로 센 것입니다. 리뷰 수(파란 실선)는 왼쪽 눈금, 비추천 비율(보라 점선)은 오른쪽 눈금으로 읽습니다.</p>
        <p><b>수집 범위</b><br>${scope(V).text}</p>
        <p><b>표본과 AI 분류</b><br>주제 수치는 AI가 원문을 분류한 결과입니다.${counts.themed != null ? ` 수집 ${num(counts.collected)}건 중 AI가 분석한 글은 ${num(counts.analyzed)}건이고, 그중 주제가 붙은 글은 ${num(counts.themed)}건입니다. 이 화면의 숫자는 그 ${num(counts.themed)}건에서 나옵니다.` : ''} 너무 짧아 제외한 글 ${num(counts.short_excluded ?? (counts.collected - counts.analyzed))}건${counts.analysis_missing_eligible ? `, 분석 대상인데 결과가 없는 글 ${num(counts.analysis_missing_eligible)}건` : ''}은 주제 집계에 포함되지 않습니다. 플레이 시간별 비추천율은 수집 리뷰 전체의 Steam 추천 여부로 계산합니다.${counts.skipped_analysis ? ` 읽을 수 없거나 원문이 없는 분석 ${num(counts.skipped_analysis)}건 제외.` : ''}${evidence.merges?.length ? ` AI가 나눈 주제 중 같은 리뷰에 80% 이상 함께 붙은 ${num(evidence.merges.length)}개는 규칙으로 합쳤습니다(분석 방법 참고).` : ''}</p>
      </div></dialog>
      <dialog class="rd-dialog" id="rdEvidenceDialog" aria-labelledby="rdDialogTitle"><div class="rd-dialog-head"><div><h2 id="rdDialogTitle">리뷰 근거</h2><p id="rdDialogMeta"></p></div><button class="rd-btn" data-action="close-dialog" aria-label="리뷰 근거 닫기">닫기 ×</button></div><div class="rd-toggle" aria-label="주제 감성 선택"><button data-action="evidence-filter" data-sentiment="N">불만</button><button data-action="evidence-filter" data-sentiment="P">칭찬</button><button data-action="evidence-filter" data-sentiment="all">전체</button></div><div id="rdEvidenceBody" aria-live="polite"></div></dialog>`;

    /* ---- 칭찬 분석 ---- */
    if (characterRoot) characterRoot.innerHTML = `
      <div class="rd-kpis">
        ${stat('', 'up', '칭찬 언급', num(sumPos), '건', `주제 ${num(topics.filter(t => t.pos > 0).length)}개에서`)}
        ${topPos?.pos ? stat('', 'trend', '칭찬이 가장 많은 주제', esc(topPos.name), '', `칭찬 ${num(topPos.pos)}건 · 불만 ${num(topPos.neg)}건`) : ''}
        ${topFun ? stat('is-accent', 'smile', '가장 많이 즐긴 것', esc(topFun.name), '', `긍정 · 혼합 리뷰의 ${pct(topFun.share)}`) : ''}
        ${stat('is-accent', 'review', '긍정 · 혼합 리뷰', num(evidence.fun_denominator), '건', `AI 분석 ${num(counts.analyzed)}건 중`)}
      </div>
      <div class="rd-grid">
        <section class="rd-pane rd-pane-half" aria-labelledby="rdPraiseTitle">
          <header class="rd-pane-head"><div><h2 id="rdPraiseTitle">칭찬이 많은 주제</h2><p>칭찬으로 언급한 리뷰 수</p></div>${icon('up')}</header>
          <div class="rd-pane-body" id="rdPraiseChart"></div>
          <p class="rd-note">이름 아래 글은 그 주제를 칭찬한 리뷰 중 Steam 도움됨이 많은 글입니다. 주제를 대표한다는 뜻은 아닙니다. 줄을 누르면 주제별 보기에서 그 주제를 엽니다.</p>
        </section>
        <section class="rd-pane rd-pane-half" aria-labelledby="rdFunTitle">
          <header class="rd-pane-head"><div><h2 id="rdFunTitle">플레이어가 즐긴 것</h2><p>긍정·혼합 리뷰 ${num(evidence.fun_denominator)}건 · 여러 개 가능</p></div>${icon('smile')}</header>
          <div class="rd-pane-body" id="rdFunChart"></div>
        </section>
        ${(evidence.fun || []).some(f => f.example) ? `<section class="rd-pane rd-pane-wide" aria-labelledby="rdVoiceTitle">
          <header class="rd-pane-head"><div><h2 id="rdVoiceTitle">즐긴 것을 말한 리뷰</h2><p>유형마다 Steam 도움됨이 가장 많은 글 하나</p></div>${icon('review')}</header>
          <div class="rd-pane-body rd-voices">${evidence.fun.filter(f => f.example).slice(0, 4).map(f => `<figure class="rd-voice">
            <figcaption><b>${esc(f.name)}</b><span>${esc(f.desc || '')}</span></figcaption>
            <blockquote>${esc(plain(f.example.content))}${f.example.truncated ? '…' : ''}</blockquote>
            <small>${f.example.recommended ? '게임 추천' : '게임 비추천'}${f.example.hours == null ? '' : ` · ${num(Math.round(f.example.hours))}시간 플레이`}${f.example.helpful ? ` · 도움됨 ${num(f.example.helpful)}` : ''}</small>
          </figure>`).join('')}</div>
        </section>` : ''}
      </div>`;

    roots.forEach(el => {
      el.onclick = onClick;
      el.onkeydown = event => {
        if ((event.key === 'Enter' || event.key === ' ') && event.target.matches?.('.rd-dot')) { event.preventDefault(); event.target.dispatchEvent(new MouseEvent('click', {bubbles:true})); }
      };
    });
    const dialog = document.getElementById('rdEvidenceDialog');
    dialog?.addEventListener('click', event => { if (event.target === dialog) closeDialog(); });
    dialog?.addEventListener('close', () => { request?.abort(); restoreFocus?.focus(); });
    const methodDialog = document.getElementById('rdMethodDialog');
    methodDialog?.addEventListener('click', event => { if (event.target === methodDialog) closeMethod(); });
    methodDialog?.addEventListener('close', () => restoreFocus?.focus());
    // 차트는 폭에 맞춰 그린다. 숨겨진 화면은 폭을 모르므로 폭이 정해지거나 바뀌면 다시 그린다.
    sizeObserver?.disconnect();
    if (window.ResizeObserver) {
      const seen = new Map();
      sizeObserver = new ResizeObserver(entries => {
        let changed = false;
        for (const entry of entries) {
          const w = Math.round(entry.contentRect.width);
          if (w && Math.abs(w - (seen.get(entry.target) || 0)) > 24) { seen.set(entry.target, w); changed = true; }
        }
        if (changed) refresh();
      });
      ['rdTrendChart', 'rdTopicChart', 'rdMapChart'].forEach(key => { const el = document.getElementById(key); if (el) sizeObserver.observe(el); });
    }
    if (!resizeBound) {
      resizeBound = true;
      let timer;
      addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(refresh, 150); });
    }
    refresh(); renderMood(); renderDetail(); renderCohorts(); renderFun(); renderPraise();
    foldNotes();
    loadHistogram(false);
  }

  // 폭에 따라 달라지는 차트만 다시 그린다. 메뉴를 바꿀 때도 부른다.
  function refresh() {
    if (!evidence) return;
    renderTrend(); renderChart(); renderMap();
  }

  function renderChart() { renderTopics(); }

  // 반응 도넛: AI가 분석한 리뷰를 긍정·혼합·부정으로 나눈 비율
  function renderMood() {
    const target = document.getElementById('rdMoodChart'), m = evidence.mood || {};
    if (!target) return;
    const parts = [['긍정', m.P || 0, 'pos', '좋았다는 글'], ['혼합', m.M || 0, 'mix', '좋은 점과 아쉬운 점이 함께 있는 글'], ['부정', m.N || 0, 'neg', '아쉬웠다는 글'], ['분류 안 됨', m.U || 0, 'unk', '']];
    const total = parts.reduce((a, p) => a + p[1], 0);
    if (!total) { target.innerHTML = '<div class="rd-empty">반응이 분류된 리뷰가 없습니다.</div>'; return; }
    const r = 62, c = 2 * Math.PI * r; let offset = 0;
    const arcs = parts.filter(p => p[1]).map(([, v, cls]) => { const len = v / total * c, a = `<circle class="rd-donut-${cls}" cx="90" cy="90" r="${r}" stroke-dasharray="${Math.max(0, len - 2)} ${c - Math.max(0, len - 2)}" stroke-dashoffset="${-offset}"/>`; offset += len; return a; }).join('');
    target.innerHTML = `<div class="rd-mood">
        <svg class="rd-donut" viewBox="0 0 180 180" role="img" aria-label="${parts.map(p => `${p[0]} ${p[1]}건`).join(', ')}"><g transform="rotate(-90 90 90)">${arcs}</g><text class="rd-donut-total" x="90" y="88" text-anchor="middle">${num(total)}</text><text class="rd-donut-label" x="90" y="108" text-anchor="middle">분석한 리뷰</text></svg>
        <div class="rd-mood-stats">${parts.filter(p => p[1]).map(([name, v, cls, desc]) => `<div><i class="${cls}"></i><span><b>${name}</b>${desc ? `<small>${desc}</small>` : ''}</span><em>${num(v)}건</em><strong>${(v / total * 100).toFixed(1)}%</strong></div>`).join('')}</div>
      </div>`;
  }

  // 날짜별 리뷰 수와 비추천 비율: Steam이 주는 전체 리뷰(모든 언어) 집계를 막대(리뷰 수) + 선(비추천 비율)으로 그린다.
  // 서버가 저장해 둔 결과를 먼저 받고, '최신화'를 누르면 Steam에서 다시 받는다.
  async function loadHistogram(fresh) {
    const id = appId, button = document.querySelector('[data-action="trend-refresh"]');
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/games/review-histogram?app_id=${Number(id)}${fresh ? '&refresh=true' : ''}`);
      if (!response.ok) throw new Error(response.status);
      const result = await response.json();
      if (id !== appId) return;
      histogram = result;
    } catch {
      if (id !== appId) return;
      if (!histogram) histogram = {failed: true};
      else histogram.stale = true;
    }
    if (button) button.disabled = false;
    destroyTrend(); renderTrend();
  }
  function renderTrend() {
    const target = document.getElementById('rdTrendChart');
    if (!target) return;
    document.querySelectorAll('[data-action="trend"]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.unit === trendUnit)));
    if (!histogram) { if (!trendCharts.length) target.innerHTML = '<div class="rd-empty">Steam에서 가져오는 중입니다.</div>'; return; }
    const note = document.getElementById('rdTrendNote');
    if (note) note.textContent = histogram.failed ? 'Steam 전체 리뷰 · 모든 언어' : `Steam 전체 리뷰 · 모든 언어 · ${histogram.fetched_at} 기준${histogram.stale ? ' (지금은 Steam에 연결되지 않아 지난 결과입니다)' : ''}`;
    const all = trendUnit === 'all', monthly = all && histogram.rollup_type === 'month';
    const rows = (all ? histogram.rollups : histogram.recent) || [];
    const strip = document.getElementById('rdTrendStrip');
    if (histogram.failed || rows.length < 2) {
      destroyTrend();
      if (strip) strip.innerHTML = '';
      target.innerHTML = `<div class="rd-empty">${histogram.failed ? 'Steam에서 가져오지 못했습니다. 최신화를 눌러 다시 시도하세요.' : '추이를 그릴 만큼 날짜가 쌓이지 않았습니다.'}</div>`;
      return;
    }
    if (!target.clientWidth) return;                     // 숨겨진 화면에서는 폭을 모른다. 메뉴를 열 때 다시 부른다
    // 높이: 잘 보이게 키우되 화면 아래로 넘치지 않게 남은 높이에 맞춘다 (260~380)
    const height = target.clientWidth < 480 ? 260 : Math.max(260, Math.min(380, Math.floor((innerHeight - target.getBoundingClientRect().top - scrollY - 96) / 20) * 20));
    const stamp = `${trendUnit}:${rows.length}:${histogram.fetched_at}:${Math.round(target.clientWidth / 24)}:${height}`;
    if (trendCharts.length && trendStamp === stamp && target.firstChild) return;   // 기간·자료·폭이 그대로면 다시 그리지 않는다
    destroyTrend(); trendStamp = stamp;
    const narrow = target.clientWidth < 480;
    const dots = k => k.replaceAll('-', '.');
    const short = k => k.slice(5).split('-').map(Number).join('.');   // 좁은 화면: 09.03 → 9.3
    const label = k => monthly ? dots(k.slice(2, 7)) : narrow ? short(k) : all ? dots(k.slice(2)) : dots(k.slice(5));
    const every = Math.max(1, Math.ceil(rows.length / (narrow ? 4 : 16)));
    const sub = '#5F6977', text = {fontSize: '13px', colors: sub};
    const rate = r => r.up + r.down ? r.down / (r.up + r.down) * 100 : 0;
    // 하루 단위(최근 30일)는 리뷰가 100건 안팎이라 비추천 몇 건에 비율이 크게 튄다. 선은 그날까지 7일을 합쳐 계산하고, 그날 값은 설명 상자에 적는다.
    const week = i => { const part = rows.slice(Math.max(0, i - 6), i + 1), n = part.reduce((a, r) => a + r.up + r.down, 0); return n ? part.reduce((a, r) => a + r.down, 0) / n * 100 : 0; };
    const rates = rows.map((r, i) => Number((all ? rate(r) : week(i)).toFixed(1)));
    const rateTop = Math.max(5, Math.ceil(Math.max(...rates) / 5) * 5);
    // 개발사 공지 날짜. 패치 노트로 표시한 공지가 있으면 그것만, 없으면 공지 전체. 전체 기간 보기에서는 그 날짜가 든 주·달에 놓는다.
    const news = histogram.events || [], patched = news.some(e => e.patch), kind = patched ? '패치' : '공지';
    const marks = new Map();
    (patched ? news.filter(e => e.patch) : news).forEach(e => {
      const i = all ? rows.findLastIndex(r => r.date <= e.date) : rows.findIndex(r => r.date === e.date);
      if (i >= 0) marks.set(i, [...(marks.get(i) || []), e]);
    });
    const markCount = [...marks.values()].reduce((a, list) => a + list.length, 0);
    // 세로선은 축 글자의 위치로 놓인다. 그래서 축 글자는 모두 만들고, 그린 뒤에 몇 칸마다 하나만 남긴다.
    const thin = chart => chart.el.querySelectorAll('.apexcharts-xaxis-texts-g text').forEach((t, i) => { if (i % every) t.style.display = 'none'; });
    // 차트 위 요약 띠: 범례를 겸한다. 기간 합계 · 기간 전체의 비추천 비율 · 리뷰가 가장 많았던 때
    const sumAll = rows.reduce((a, r) => a + r.up + r.down, 0), sumDown = rows.reduce((a, r) => a + r.down, 0);
    // 전체 기간에서 가장 많은 달이 중앙값의 열 배를 넘으면 나머지가 바닥에 붙는다. 그때는 왼쪽 눈금을 로그(한 칸 = 10배)로 그린다.
    const totals = rows.map(r => r.up + r.down), sortedTotals = totals.filter(v => v > 0).sort((a, b) => a - b);
    const middle = sortedTotals[Math.floor(sortedTotals.length / 2)] || 1;
    const logCount = all && sortedTotals.length > 2 && sortedTotals[sortedTotals.length - 1] / middle >= 10;
    if (note && logCount) note.textContent += ' · 왼쪽 눈금은 로그(한 칸이 10배)';
    target.setAttribute('role', 'img');
    target.setAttribute('aria-label', `${all ? '전체 기간' : '최근 30일'} Steam 리뷰 추이. 리뷰 ${num(sumAll)}건, 비추천 비율 ${pct(sumAll ? sumDown / sumAll * 100 : 0)}.`);
    const peak = rows.reduce((a, r) => r.up + r.down > a.up + a.down ? r : a, rows[0]);
    if (strip) strip.innerHTML = `<div><b>${num(sumAll)}건</b><small><i class="line pos"></i>리뷰 수</small></div>
      <div><b>${pct(sumAll ? sumDown / sumAll * 100 : 0)}</b><small><i class="line neg"></i>기간 전체 비추천 비율</small></div>
      ${markCount ? `<div><b>${num(markCount)}건</b><small><i class="mark"></i>${patched ? '패치 공지' : '공지'}</small></div>` : ''}
      <div class="rd-strip-end"><b>${dots(monthly ? peak.date.slice(0, 7) : peak.date)}${all && !monthly ? ' 주' : ''}</b><small>리뷰가 가장 많은 ${monthly ? '달' : all ? '주' : '날'} · ${num(peak.up + peak.down)}건</small></div>`;
    target.innerHTML = '';
    trendCharts.push(new ApexCharts(target, {
      chart: {type: 'line', height, fontFamily: 'inherit', toolbar: {show: false}, zoom: {enabled: false}, parentHeightOffset: 0, events: {mounted: thin, updated: thin}},
      // 선 두 개. 눈금이 다르므로 색과 선 모양을 함께 다르게 한다: 파란 실선 = 리뷰 수(왼쪽 눈금), 보라 점선 = 비추천 비율(오른쪽 눈금)
      series: [{name: '리뷰 수', type: 'area', data: logCount ? totals.map(v => Math.max(v, 1)) : totals}, {name: '비추천 비율', type: 'line', data: rates}],
      // 리뷰 수는 굵은 선에 옅은 면을 깔아 주인공으로, 비율은 가는 점선으로 뒤에 둔다. 두 선은 눈금이 달라 높이를 서로 견줄 수 없다.
      fill: {type: ['gradient', 'solid'], opacity: [1, 1], gradient: {shadeIntensity: 0, opacityFrom: .22, opacityTo: 0, stops: [0, 100]}},
      colors: ['#2794EB', '#8D61A5'],
      stroke: {width: [4, 2], curve: 'monotoneCubic', lineCap: 'butt', dashArray: [0, 5]},
      annotations: {xaxis: [...marks.keys()].map(i => ({x: label(rows[i].date), borderColor: '#5F6977', strokeDashArray: 3,
        label: all ? {text: ''} : {text: kind, orientation: 'horizontal', borderWidth: 0, offsetY: -2, style: {background: '#191F28', color: '#fff', fontSize: '13px', fontWeight: 500, padding: {left: 6, right: 6, top: 2, bottom: 3}}}}))},
      markers: {size: 0, colors: ['#fff'], strokeColors: ['#2794EB', '#8D61A5'], strokeWidth: 2, hover: {size: 6}},
      dataLabels: {enabled: false},
      grid: {borderColor: '#EEF0F2', padding: {left: 16, right: 16}},
      xaxis: {categories: rows.map(r => label(r.date)), labels: {rotate: 0, hideOverlappingLabels: false, style: text}, axisBorder: {color: '#D9DEE3'}, axisTicks: {show: false}, tooltip: {enabled: false}},
      yaxis: [
        {seriesName: '리뷰 수', ...(logCount ? {logarithmic: true, logBase: 10, min: 10 ** Math.floor(Math.log10(sortedTotals[0])), max: 10 ** Math.ceil(Math.log10(sortedTotals[sortedTotals.length - 1]))} : {min: 0}), labels: {style: {fontSize: '13px', colors: '#1A73C4'}, formatter: v => num(Math.round(v))}},
        {seriesName: '비추천 비율', opposite: true, min: 0, max: rateTop, tickAmount: 4, labels: {style: {fontSize: '13px', colors: '#74498C'}, formatter: v => `${Math.round(v)}%`}}],
      legend: {show: false},
      tooltip: {shared: true, intersect: false, style: {fontSize: '13px'},
        x: {formatter: (v, o) => { const r = rows[o.dataPointIndex]; const m = marks.get(o.dataPointIndex); return r ? `${dots(monthly ? r.date.slice(0, 7) : r.date)}${all && !monthly ? ' 주' : ''}${m ? ` · ${kind}: ${esc(m[0].title)}${m.length > 1 ? ` 외 ${m.length - 1}건` : ''}` : ''}` : ''; }},
        y: {formatter: (v, o) => { const r = rows[o.dataPointIndex]; if (!r) return ''; return o.seriesIndex ? (all ? `${v.toFixed(1)}% (${num(r.down)}/${num(r.up + r.down)}건)` : `7일 평균 ${v.toFixed(1)}% · 이날 ${rate(r).toFixed(1)}% (${num(r.down)}/${num(r.up + r.down)}건)`) : `${num(r.up + r.down)}건 (추천 ${num(r.up)} · 비추천 ${num(r.down)})`; }}},
    }));
    trendCharts.forEach(c => c.render());
  }
  function destroyTrend() {
    trendCharts.forEach(c => c.destroy());
    trendCharts = []; trendStamp = '';
  }

  // IPA 매트릭스(IPA): x = 언급 수, y = 불만 비율, 원 크기 = 언급 수. 점선은 언급 수 중앙값과 불만 50%.
  function renderMap() {
    const target = document.getElementById('rdMapChart');
    if (!target) return;
    // 높이: 옆의 주제 줄 카드와 비슷하게 300~620px
    // 그릴 폭은 카드 안쪽 여백을 뺀 값이다. 여백까지 넣으면 그림이 줄어들어 글자가 정한 크기보다 작아진다.
    const inner = () => { const cs = getComputedStyle(target); return target.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight); };
    const W = Math.max(300, inner() || 560);
    const besideHeight = () => { const el = document.getElementById('rdTopicChart'); return el?.offsetHeight ? el.offsetHeight + (el.previousElementSibling?.offsetHeight || 0) + 8 : 0; };
    const beside = besideHeight();
    const H = W < 480 ? 320 : Math.round(Math.max(300, Math.min(620, beside || W * 1.05)));
    requestAnimationFrame(() => {
      const width = Math.max(300, inner() || W);
      if (!renderMap.again && (Math.abs(width - W) > 8 || (W >= 480 && Math.abs(besideHeight() - beside) > 8))) { renderMap.again = true; renderMap(); renderMap.again = false; }
    });
    target.innerHTML = mapSVG(W, H) || '<div class="rd-empty">분류된 주제가 아직 없습니다.</div>';
  }

  // IPA 매트릭스 SVG 문자열. 화면과 한 장 보고서가 같은 그림을 쓴다.
  // 가로 = 언급 수(중요도), 세로 = 불만 비율(성과의 반대). 기준선은 언급 수 중앙값과 불만 50%.
  function mapSVG(W, H) {
    const topics = evidence.themes.filter(t => t.mentions > 0);
    if (!topics.length) return '';
    const mentions = topics.map(t => t.mentions);
    const maxM = Math.max(...mentions), minM = Math.min(...mentions);
    // 언급 수가 네 배 이상 벌어지면 로그 눈금. 선형이면 적게 언급된 주제가 왼쪽 끝에 몰린다.
    const logScale = maxM / minM >= 4;
    const L = 40, R = 12, T = 28, B = logScale ? (W < 480 ? 40 : 46) : 30, pw = W - L - R, ph = H - T - B;
    const sorted = [...mentions].sort((a,b) => a - b);
    const median = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length/2 - 1] + sorted[sorted.length/2]) / 2;
    let x, xTicks = [], xMax = 0;
    if (logScale) {
      const lo = Math.log(minM / 1.35), span = Math.log(maxM * 1.3) - lo;
      x = v => L + pw * (Math.log(v) - lo) / span;
      for (let p = 10 ** Math.floor(Math.log10(minM / 1.35)); p <= maxM * 1.3; p *= 10)
        for (const m of [1, 2, 5]) if (m * p >= minM / 1.35 && m * p <= maxM * 1.3) xTicks.push(m * p);
    } else {
      const step = maxM > 200 ? 100 : maxM > 60 ? 50 : 10;
      xMax = Math.ceil(maxM * 1.18 / step) * step;
      x = v => L + pw * v / xMax;
    }
    const y = v => T + ph * (1 - v);
    const xm = x(median), ym = y(.5);
    const share = themeNegShare;
    // 넓은 화면에서 원만 작게 남지 않도록 차트 폭에 맞춰 키운다(최대 1.6배)
    // 원 넓이가 언급 리뷰 수에 비례한다(반지름 = √건수). 가장 큰 원은 그림 짧은 변의 6%쯤으로 잡아 원끼리 덜 겹치게 한다.
    const biggest = Math.max(12, Math.min(22, Math.min(pw, ph) * .06));
    const radius = t => Math.max(4, biggest * Math.sqrt(t.mentions / maxM));
    const dots = topics.map(t => ({t, r: role(t), cx: x(t.mentions), cy: y(share(t)), rad: radius(t)}));
    const hero = r => ['concern','strength','watch'].includes(r);
    // 라벨: 중요한 주제부터 빈 자리에 놓고, 자리가 없으면 생략(마우스를 올리면 이름이 보임)
    const textW = (str, size) => [...str].reduce((w, ch) => w + (/[ㄱ-힝]/.test(ch) ? size : size * .6), 0);
    const boxes = [], labels = [];
    const hits = (b, edge = W - R) => b.x < L || b.x + b.w > edge || b.y < T || b.y + b.h > H - B + 4
      || boxes.some(o => b.x < o.x + o.w && b.x + b.w > o.x && b.y < o.y + o.h && b.y + b.h > o.y)
      || dots.some(d => { const nx = Math.max(b.x, Math.min(d.cx, b.x + b.w)), ny = Math.max(b.y, Math.min(d.cy, b.y + b.h)); return Math.hypot(d.cx - nx, d.cy - ny) < d.rad + 1; });
    const order = [...dots].sort((a,b) => hero(b.r) - hero(a.r) || (b.t.name === selected) - (a.t.name === selected) || b.t.mentions - a.t.mentions);
    const place = (d, nameOnly = false) => {
      const big = hero(d.r);
      if (W < 480 && !big) return;
      const size = big ? 14 : 13, sub = big && !nameOnly ? `${num(d.t.mentions)}건 · 불만 ${Math.round(share(d.t) * 100)}%` : '';
      const w = Math.max(textW(d.t.name, size), sub ? textW(sub, 13) : 0), h = sub ? 33 : 17;
      const g = d.rad + (big ? 9 : 5);
      const k = g * .72;
      const spots = [[d.cx + g, d.cy - h / 2, 'start'], [d.cx - g - w, d.cy - h / 2, 'end'], [d.cx - w / 2, d.cy - g - h, 'middle'], [d.cx - w / 2, d.cy + g, 'middle'],
        [d.cx + k, d.cy - k - h, 'start'], [d.cx - k - w, d.cy - k - h, 'end'], [d.cx + k, d.cy + k, 'start'], [d.cx - k - w, d.cy + k, 'end']];
      let placed = false;
      for (const [bx, by, anchor] of spots) {
        const b = {x: bx, y: by, w, h};
        // 지킬 것·고칠 것 이름은 반드시 보이도록 카드 여백까지 허용
        if (hits(b, big ? W + 18 : W - R)) continue;
        boxes.push(b);
        const tx = anchor === 'start' ? bx : anchor === 'end' ? bx + w : bx + w / 2;
        placed = true;
        labels.push(`<text class="rd-dot-label is-${d.r}" data-action="select" data-theme="${esc(d.t.name)}" x="${tx}" y="${by + size - 1}" text-anchor="${anchor}" font-size="${size}">${esc(d.t.name)}</text>${sub ? `<text class="rd-dot-sub" data-action="select" data-theme="${esc(d.t.name)}" x="${tx}" y="${by + size + 15}" text-anchor="${anchor}">${sub}</text>` : ''}`);
        break;
      }
      if (!placed && sub) place(d, true);   // 건수 줄까지 놓을 자리가 없으면 이름만 놓는다
    };
    order.filter(d => hero(d.r)).forEach(d => place(d));   // 강조한 주제의 이름이 영역 이름에 자리를 뺏기면 안 된다
    // 네 영역. 오른쪽 = 언급 수 중앙값 이상, 위 = 불만 50% 초과
    const quads = [
      {cls:'is-fix', right:true, top:true}, {cls:'is-keep', right:true, top:false},
      {cls:'is-watch', right:false, top:true}, {cls:'is-small', right:false, top:false}].map(q => ({...q,
        x0: q.right ? xm : L, x1: q.right ? W - R : xm, y0: q.top ? T : ym, y1: q.top ? ym : H - B}));
    // 영역 이름: 네 칸 모두 같은 크기의 바탕 글자. 칸 안에서 원과 겹치지 않는 자리를 찾아 놓고,
    // 주제 이름이 그 위에 올라오지 않도록 자리를 맡아 둔다.
    const QUAD_TITLE = {'is-fix': '집중 개선', 'is-keep': '유지 강화', 'is-watch': '낮은 우선순위', 'is-small': '작은 강점'};
    const narrowest = Math.min(...quads.map(q => q.x1 - q.x0));
    const quadSize = [16, 14].find(size => textW('낮은 우선순위', size) + 16 <= narrowest) || 0;
    const quadLabels = !quadSize ? '' : quads.map(q => {
      const title = QUAD_TITLE[q.cls], w = textW(title, quadSize), h = quadSize + 6, pad = 10;
      const xs = [(q.x0 + q.x1 - w) / 2, q.right ? q.x1 - pad - w : q.x0 + pad, q.right ? q.x0 + pad : q.x1 - pad - w];
      const ys = [(q.y0 + q.y1 - h) / 2, q.y0 + (q.y1 - q.y0) * .25 - h / 2, q.y0 + (q.y1 - q.y0) * .75 - h / 2, q.y0 + pad, q.y1 - pad - h];
      const spots = ys.flatMap(by => xs.map(bx => ({x: bx, y: by, w, h})));
      const inside = b => b.x >= q.x0 && b.x + b.w <= q.x1 && b.y >= q.y0 && b.y + b.h <= q.y1;
      const box = spots.find(b => inside(b) && !hits(b)) || spots[0];
      boxes.push(box);
      return `<text class="rd-q-label ${q.cls}" x="${box.x + w / 2}" y="${box.y + quadSize}" text-anchor="middle" font-size="${quadSize}">${title}</text>`;
    }).join('');

    order.filter(d => !hero(d.r)).forEach(d => place(d));
    const tick = (tx, ty, str, anchor = 'end') => `<text class="rd-map-tick" x="${tx}" y="${ty}" text-anchor="${anchor}">${str}</text>`;
    const axisName = W < 480 ? (logScale ? '언급 수 · 로그 눈금' : `${num(xMax)}건`) : logScale ? '언급 리뷰 수 →' : `언급 리뷰 수 → ${num(xMax)}건`;
    const xGrid = xTicks.map(v => `<line x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${H - B}" class="rd-map-grid"/>`).join('');
    const xTickText = xTicks.filter(v => Math.abs(x(v) - xm) > 26).map(v => tick(x(v), H - B + 16, num(v), 'middle')).join('');
    return `<svg class="rd-map" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="group" aria-label="IPA 매트릭스: 언급 수와 불만 비율">
      <defs>
        <linearGradient id="rdQFix" x1="1" y1="0" x2="0" y2="1"><stop offset="0" class="rd-q-stop-fix" stop-opacity=".09"/><stop offset="1" class="rd-q-stop-fix" stop-opacity=".03"/></linearGradient>
        <linearGradient id="rdQKeep" x1="1" y1="1" x2="0" y2="0"><stop offset="0" class="rd-q-stop-keep" stop-opacity=".09"/><stop offset="1" class="rd-q-stop-keep" stop-opacity=".03"/></linearGradient>
      </defs>
      <rect x="${L}" y="${T}" width="${xm - L}" height="${ph}" class="rd-q-rest"/>
      <rect x="${xm}" y="${T}" width="${W - R - xm}" height="${ym - T}" fill="url(#rdQFix)"/>
      <rect x="${xm}" y="${ym}" width="${W - R - xm}" height="${H - B - ym}" fill="url(#rdQKeep)"/>
      ${xGrid}
      <line x1="${L}" x2="${W - R}" y1="${ym}" y2="${ym}" class="rd-map-mid"/>
      <line x1="${xm}" x2="${xm}" y1="${T}" y2="${H - B}" class="rd-map-mid"/>
      <line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" class="rd-map-axis"/>
      ${quadLabels}
      ${tick(L - 6, T + 4, '100%')}${tick(L - 6, ym + 4, '50%')}${tick(L - 6, H - B + 4, '0%')}
      ${logScale ? xTickText : tick(L, H - 8, '0', 'start')}${tick(xm, H - 8, `중앙값 ${num(Math.round(median))}건`, 'middle')}${tick(W - R, H - 8, axisName, 'end')}
      ${[...dots].sort((a,b) => (a.t.name === selected || hero(a.r)) - (b.t.name === selected || hero(b.r)) || b.rad - a.rad).map(d => `<g class="rd-dot is-${d.r}" data-action="select" data-theme="${esc(d.t.name)}" tabindex="0" role="button" aria-pressed="${selected === d.t.name}" aria-label="${esc(d.t.name)}: 언급 ${d.t.mentions}건, 불만 ${Math.round(share(d.t) * 100)}%, 칭찬 ${d.t.pos}건, 불만 ${d.t.neg}건"><title>${esc(d.t.name)} · 언급 ${num(d.t.mentions)}건 · 칭찬 ${num(d.t.pos)} · 불만 ${num(d.t.neg)} (${Math.round(share(d.t) * 100)}%)</title>${d.rad < 12 ? `<circle cx="${d.cx}" cy="${d.cy}" r="${d.rad + 6}" class="rd-dot-hit"/>` : ''}${hero(d.r) ? `<circle cx="${d.cx}" cy="${d.cy}" r="${d.rad + 8}" class="rd-dot-halo"/>` : ''}<circle cx="${d.cx}" cy="${d.cy}" r="${d.rad + 4}" class="rd-dot-ring"/><circle cx="${d.cx}" cy="${d.cy}" r="${d.rad}" class="rd-dot-mark"/></g>`).join('')}
      <g aria-hidden="true">${labels.join('')}</g>
    </svg>`;
  }

  // 주제 줄: 언급이 많은 순서. 막대 길이 = 언급한 리뷰 수(파랑 칭찬 · 보라 불만), 오른쪽에 건수와 불만 비율.
  function renderTopics() {
    const target = document.getElementById('rdTopicChart');
    if (!target) return;
    const topics = evidence.themes.filter(t => t.mentions > 0).sort((a,b) => b.mentions - a.mentions || a.name.localeCompare(b.name));
    if (!topics.length) { target.innerHTML = '<div class="rd-empty">분류된 주제가 아직 없습니다.</div>'; return; }
    const max = Math.max(...topics.map(t => t.pos + t.neg), 1);
    target.innerHTML = `<div class="rd-rank-head" aria-hidden="true"><span>주제</span><span>칭찬 · 불만으로 언급한 리뷰</span><span>언급</span><span>불만 비율</span></div>
      <div class="rd-ranks">${topics.map(t => { const share = Math.round(themeNegShare(t) * 100);
        return `<button type="button" class="rd-rank" data-action="select" data-theme="${esc(t.name)}" aria-pressed="${selected === t.name}" aria-label="${esc(t.name)}: 언급 ${t.mentions}건, 칭찬 ${t.pos}건, 불만 ${t.neg}건, 칭찬과 불만 언급 중 불만 ${share}%${t.neg_range ? `, 건수로 흔들리는 범위 ${Math.round(t.neg_range[0])}에서 ${Math.round(t.neg_range[1])}%${t.sure === false ? ', 칭찬이 많은지 불만이 많은지 불확실' : ''}` : ''}" title="${esc(t.name)} · 칭찬 ${num(t.pos)} · 불만 ${num(t.neg)}${t.neg_range ? ` · 건수로 흔들리는 범위 ${Math.round(t.neg_range[0])}–${Math.round(t.neg_range[1])}%` : ''}">
          <span class="rd-rank-name">${esc(t.name)}</span>
          <span class="rd-rank-track"><span style="width:${(t.pos + t.neg) / max * 100}%">${t.pos ? `<i class="pos" style="flex:${t.pos}"></i>` : ''}${t.neg ? `<i class="neg" style="flex:${t.neg}"></i>` : ''}</span></span>
          <span class="rd-rank-count">${num(t.mentions)}건</span>
          <span class="rd-rank-share ${t.neg > t.pos && t.sure !== false ? 'is-neg' : ''}">${t.sure === false ? '<small>불확실</small>' : ''}${share}%</span>
        </button>`; }).join('')}</div>${evidence.counts?.off_list_complaints ? `<p class="rd-rank-foot">구체적인 불만인데 위 주제에 들지 않은 리뷰가 ${num(evidence.counts.off_list_complaints)}건 있습니다. 이 불만은 주제 목록과 매트릭스에 나오지 않습니다.</p>` : ''}`;
  }

  // 선택 주제: 칭찬/불만 비율, 불만이 나오는 플레이 구간, AI 요약, 실제 리뷰 한 줄
  function renderDetail() {
    const t = themeByName(selected), target = document.getElementById('rdDetail');
    if (!t) { target.innerHTML = '<div class="rd-empty">확인할 주제가 없습니다.</div>'; return; }
    const r = role(t);
    const label = t.neg > t.pos ? '불만이 더 많음' : '칭찬이 더 많음';
    const definition = data.themes?.find(row => row.name === t.name)?.desc;
    const action = data.actions?.find(row => row.theme === t.name);
    const lead = t.neg > t.pos ? 'N' : 'P';
    const quote = t.examples?.[lead]?.[0] || t.examples?.[lead === 'N' ? 'P' : 'N']?.[0];
    const cells = (t.cells || []).map((c, i) => ({...c, label: evidence.cohorts[i]?.label || ''}));
    const reliable = cells.filter(c => c.denominator >= 30 && c.rate != null);
    const cellMax = Math.max(1, ...reliable.map(c => c.rate));
    const when = t.neg && cells.length ? `<section class="rd-sec">
        <h3>${icon('clock')}이 불만이 나오는 플레이 구간</h3>
        <div class="rd-cols" role="img" aria-label="${cells.map(c => `${c.label} ${c.denominator ? pct(c.rate) : '자료 없음'}`).join(', ')}">
          ${cells.map(c => { const small = c.denominator < 30; const h = small || c.rate == null ? 0 : Math.max(4, c.rate / cellMax * 100);
            const top = !small && c.rate != null && c.rate === cellMax;
            return `<div class="rd-col ${small ? 'is-small' : ''}${top ? ' is-max' : ''}" title="${esc(c.label)} · ${num(c.count)} / ${num(c.denominator)}건${small ? ' · 표본 적음' : ''}"><span class="rd-col-val">${small ? '표본 적음' : pct(c.rate)}</span><span class="rd-col-track"><i style="height:${h}%"></i></span><span class="rd-col-label">${esc(c.label)}</span></div>`; }).join('')}
        </div>
        <p class="rd-note">구간별 AI 분석 리뷰 중 이 주제 불만 비율 · 30건 미만 구간은 표시하지 않음</p>
      </section>` : '';
    target.className = `rd-pane rd-focus is-${r}`;
    target.innerHTML = `
      <header class="rd-pane-head">
        <div><h2>선택한 주제 · ${esc(t.name)}<span class="rd-focus-badge">${label}</span></h2><p>${definition ? `${esc(definition)} · ` : ''}칭찬 ${num(t.pos)}건 · 불만 ${num(t.neg)}건${t.neg && t.negative_recommended != null ? ` (불만을 쓴 리뷰 중 ${num(t.negative_recommended)}건은 게임을 추천)` : ''}</p></div>
      </header>
      <div class="rd-focus-grid">
        ${when}
        ${action?.prob || action?.why || action?.fix?.length ? `<section class="rd-sec rd-why"><h3>${icon('bulb')}AI가 요약한 내용</h3>${action.prob ? `<p><b>무슨 일인가</b>${esc(action.prob)}</p>` : ''}${action.why ? `<p><b>리뷰가 말하는 원인</b>${esc(action.why)}</p>` : ''}${action.fix?.length ? `<p><b>리뷰에서 나온 제안</b></p><ul>${action.fix.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}<p class="rd-note">원문으로 확인한 뒤 판단하세요.</p></section>` : ''}
        <section class="rd-sec">
          ${quote ? `<h3>${icon('review')}대표 리뷰 <small>${quote.recommended ? '게임 추천' : '게임 비추천'}${quote.hours == null ? '' : ` · ${num(Math.round(quote.hours))}시간 플레이`}</small></h3><blockquote class="rd-quote">${esc(plain(quote.content))}${quote.truncated ? '…' : ''}</blockquote>` : `<h3>${icon('review')}리뷰 원문</h3>`}
          <div class="rd-focus-actions">
            ${t.neg ? `<button class="rd-btn ${lead === 'N' ? 'primary' : ''}" data-action="evidence" data-sentiment="N">불만 리뷰 ${num(t.neg)}건</button>` : ''}
            ${t.pos ? `<button class="rd-btn ${lead === 'P' ? 'primary' : ''}" data-action="evidence" data-sentiment="P">칭찬 리뷰 ${num(t.pos)}건</button>` : ''}
          </div>
        </section>
      </div>`;
  }

  function renderCohorts() {
    if (!evidence) return;
    const base = evidence.sample_negative_rate;
    const rows = evidence.cohorts.filter(c => c.n > 0);
    const target = document.getElementById('rdCohortChart');
    if (!target) return;
    if (!rows.length) { target.innerHTML = '<div class="rd-empty">작성 당시 플레이 시간이 있는 리뷰가 없습니다.</div>'; return; }
    const max = Math.max(10, base || 0, ...rows.filter(c => !c.small).map(c => c.negative_rate || 0)) * 1.15;
    const x = v => clamp(v / max * 100);
    target.innerHTML = `<div class="rd-cohorts">${rows.map(c => `<div class="rd-cohort ${c.small ? 'is-small' : ''}">
        <span class="rd-cohort-label"><b>${esc(c.label)}</b><small>${num(c.n)}건</small></span>
        <span class="rd-cohort-track" role="img" aria-label="${esc(c.label)}, ${num(c.n)}건 중 비추천 ${num(c.negative)}건, ${pct(c.negative_rate)}${c.small ? ', 표본 적음' : ''}">
          ${base == null ? '' : `<i class="rd-cohort-base" style="left:${x(base)}%"></i>`}
          ${c.negative_rate == null || c.small ? '' : `<i class="rd-cohort-fill" style="width:${x(c.negative_rate)}%"></i>`}
        </span>
        <span class="rd-cohort-val">${c.small ? `<small>표본 적음 (${num(c.negative)}/${num(c.n)}건)</small>` : pct(c.negative_rate)}</span>
        ${!c.small && c.top_neg?.length ? `<span class="rd-cohort-top">많이 나온 불만 · ${c.top_neg.map(t => `${esc(t.name)} ${num(t.count)}건`).join(' · ')}</span>` : ''}
      </div>`).join('')}</div>
      <p class="rd-note">막대는 그 시간대에 쓴 리뷰 중 Steam 비추천 비율입니다. 플레이 시간이 짧아서 비추천했다는 뜻은 아닙니다.</p>`;
  }

  // 재미 유형: 가장 많이 언급된 것만 진한 파랑, 나머지는 옅게. 막대 길이 = 긍정·혼합 리뷰 중 비율.
  // 칭찬이 많은 주제: 칭찬으로 언급한 리뷰가 많은 순서. 이름 아래에 그 주제를 칭찬한 실제 글 한 줄.
  const plain = text => String(text || '').replace(/\[\/?[a-z0-9*]+(=[^\]]*)?\]/gi, ' ').replace(/\s+/g, ' ').trim();
  function renderPraise() {
    const target = document.getElementById('rdPraiseChart');
    if (!target) return;
    const rows = evidence.themes.filter(t => t.pos > 0).sort((a,b) => b.pos - a.pos || a.name.localeCompare(b.name)).slice(0, 5);
    if (!rows.length) { target.innerHTML = '<div class="rd-empty">칭찬으로 분류된 주제가 없습니다.</div>'; return; }
    const max = rows[0].pos;
    // 같은 글이 두 주제에 되풀이되지 않게, 아직 쓰지 않은 글을 고른다
    const used = new Set(), pick = t => { const ex = (t.examples?.P || []).find(x => !used.has(x.id)); if (ex) used.add(ex.id); return ex ? plain(ex.content) : ''; };
    target.innerHTML = `<div class="rd-praises">${rows.map((t, i) => { const quote = pick(t);
      return `<button type="button" class="rd-praise ${i ? '' : 'is-top'}" data-action="open-topic" data-theme="${esc(t.name)}" aria-label="${esc(t.name)}: 칭찬 ${t.pos}건">
        <span class="rd-praise-name">${esc(t.name)}</span>
        <span class="rd-praise-track"><i style="width:${t.pos / max * 100}%"></i></span>
        <span class="rd-praise-count">${num(t.pos)}건</span>
        ${quote ? `<span class="rd-praise-quote">“${esc(quote)}”</span>` : ''}
      </button>`; }).join('')}</div>`;
  }

  function renderFun() {
    const target = document.getElementById('rdFunChart');
    const rows = evidence.fun || [];
    if (!rows.length) { target.innerHTML = '<div class="rd-empty">재미 유형이 분류된 리뷰가 없습니다.</div>'; return; }
    const max = Math.max(...rows.map(f => f.share || 0), 1);
    target.innerHTML = `<div class="rd-funs">${rows.map((f, i) => `<div class="rd-fun-row ${i ? '' : 'is-top'}" title="${esc(f.desc)} · ${num(f.count)} / ${num(evidence.fun_denominator)}건">
        <span class="rd-fun-name"><b>${esc(f.name)}</b><small>${esc(f.desc || '')}</small></span>
        <span class="rd-fun-track"><i style="width:${clamp(f.share / max * 100)}%"></i></span>
        <span class="rd-fun-val">${pct(f.share)}</span></div>`).join('')}</div>`;
  }

  function onClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    if (action === 'select') {
      if (!themeByName(button.dataset.theme)) return;
      selected = button.dataset.theme; updateURL(); renderChart(); renderMap(); renderDetail();
      // 근거 카드는 맨 아래 줄에 있으므로 보이는 곳까지 내린다
      document.getElementById('rdDetail')?.scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
    } else if (action === 'open-topic') {
      // 개요의 주제 카드 → 주제별 보기 화면에서 그 주제를 고른 채로 연다
      if (themeByName(button.dataset.theme)) { selected = button.dataset.theme; updateURL(); renderDetail(); }
      document.querySelector('.nav-item[data-page="topics"]')?.click();
    }
    else if (action === 'trend') { trendUnit = button.dataset.unit; renderTrend(); }
    else if (action === 'trend-refresh') loadHistogram(true);
    else if (action === 'method') { restoreFocus=button; document.getElementById('rdMethodDialog').showModal(); }
    else if (action === 'evidence') {
      restoreFocus = button; dialogTheme=selected; dialogSentiment=button.dataset.sentiment; dialogPage=1;
      document.getElementById('rdEvidenceDialog').showModal(); loadEvidence();
    } else if (action === 'close-dialog') closeDialog();
    else if (action === 'close-method') closeMethod();
    else if (action === 'evidence-filter') { dialogSentiment=button.dataset.sentiment; dialogPage=1; loadEvidence(); }
    else if (action === 'evidence-page') { dialogPage=Number(button.dataset.page); loadEvidence(); }
    else if (action === 'retry-evidence') loadEvidence();
  }
  function closeDialog() { document.getElementById('rdEvidenceDialog').close(); }
  // 불만 분석에서 한 주제의 리뷰 원문을 연다. 근거 창이 주제별 보기에 있어서 그 화면으로 옮겨 간다.
  function openTopic(name, sentiment) {
    if (!themeByName(name)) return;
    selected = name; updateURL(); renderChart(); renderMap(); renderDetail();
    document.querySelector('.nav-item[data-page="topics"]')?.click();
    dialogTheme = name; dialogSentiment = sentiment; dialogPage = 1;
    document.getElementById('rdEvidenceDialog').showModal(); loadEvidence();
  }

  function closeMethod() { document.getElementById('rdMethodDialog').close(); }
  async function loadEvidence() {
    request?.abort(); request = new AbortController();
    const controller=request, body=document.getElementById('rdEvidenceBody');
    document.getElementById('rdDialogTitle').textContent = `${dialogTheme} · 리뷰 근거`;
    document.getElementById('rdDialogMeta').textContent = 'AI 주제 분류 · 원문을 직접 확인하세요 · 공감 수, 최신순';
    document.querySelectorAll('[data-action="evidence-filter"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.sentiment===dialogSentiment)));
    body.innerHTML='<p class="rd-empty">원문을 불러오는 중…</p>';
    try {
      const params=new URLSearchParams({app_id:appId,theme:dialogTheme,sentiment:dialogSentiment,page:dialogPage});
      const response=await fetch(`/dashboard/evidence?${params}`,{signal:controller.signal});
      if (!response.ok) throw new Error('evidence unavailable');
      const result=await response.json();
      if(controller!==request) return;
      body.innerHTML = result.reviews.map(r=>`<article class="rd-review"><header><b>${r.recommended?'게임 추천':'게임 비추천'}</b><span>작성 당시 ${r.hours==null?'플레이 시간 미상':`${num(Math.round(r.hours*10)/10)}시간`}</span><span>공감 ${num(r.helpful)}</span></header><p>${esc(plain(r.content))}${r.truncated?'…':''}</p>${r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Steam에서 원문 보기 ↗</a>`:''}</article>`).join('') || '<p class="rd-empty">이 분류에 해당하는 리뷰가 없습니다.</p>';
      const pages=Math.max(1,Math.ceil(result.total/result.page_size));
      body.innerHTML+=`<div class="rd-pagination"><button class="rd-btn" data-action="evidence-page" data-page="${dialogPage-1}" ${dialogPage<=1?'disabled':''}>이전</button><span>${num(result.total)}건 · ${dialogPage} / ${pages}</span><button class="rd-btn" data-action="evidence-page" data-page="${dialogPage+1}" ${dialogPage>=pages?'disabled':''}>다음</button></div>`;
    } catch(error) {
      if(error.name==='AbortError') return;
      body.innerHTML='<p class="rd-empty">원문을 불러오지 못했습니다. <button class="rd-btn" data-action="retry-evidence">다시 시도</button></p>';
    }
  }
  /* ---------- 한 장 보고서: A4 한 쪽. 새 창에서 열고 인쇄(PDF 저장) ---------- */
  function reportHTML() {
    if (!evidence) return '';
    const s = strength(), c = concern() || loudest(), counts = evidence.counts;
    const game = data.game?.name || '게임';
    const period = evidence.period ? `${evidence.period.start.replaceAll('-','.')} – ${evidence.period.end.replaceAll('-','.')}` : '';
    const action = c && data.actions?.find(a => a.theme === c.name);
    const quote = (t, k) => t?.examples?.[k]?.[0]?.content;
    const topicBox = (t, kind) => {
      if (!t) return '';
      const q = quote(t, kind === 'keep' ? 'P' : 'N');
      return `<div class="rpt-topic is-${kind}">
        <span class="rpt-tag">${kind === 'keep' ? '칭찬 최다' : '불만 최다'}</span><h3>${esc(t.name)}</h3>
        <p class="rpt-num">${kind === 'keep' ? `칭찬 ${Math.round((1 - themeNegShare(t)) * 100)}%` : `불만 ${Math.round(themeNegShare(t) * 100)}%`} <small>· 언급 ${num(t.mentions)}건 (칭찬 ${num(t.pos)} / 불만 ${num(t.neg)})</small></p>
        ${kind === 'fix' && action?.prob ? `<p><b>문제</b>${esc(action.prob)}</p>` : ''}
        ${kind === 'fix' && action?.why ? `<p><b>원인</b>${esc(action.why)}</p>` : ''}
        ${kind === 'fix' && action?.fix?.length ? `<p><b>유저 제안</b>${action.fix.map(esc).join(' · ')}</p>` : ''}
        ${q ? `<blockquote>“${esc(q.slice(0, 120))}${q.length > 120 ? '…' : ''}”</blockquote>` : ''}</div>`;
    };
    const deep = window.ReviewPages?.deepLines(evidence.deep) || [];
    const design = (data.design_log || []).filter(r => ['무엇을', '얼마나', '주제 합치기', '강조한 주제'].includes(r.step));
    return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(game)} 리뷰 진단 보고서</title>
<link rel="stylesheet" href="${location.origin}/static/review-dashboard.css">
<style>
@page { size:A4; margin:10mm; }
body { margin:0; background:#F2F4F6; font-family:system-ui,-apple-system,"Segoe UI","Noto Sans KR",sans-serif; }
.rpt { width:186mm; min-height:273mm; margin:16px auto; padding:10mm; box-sizing:border-box; background:#fff; color:#333D4B; font-size:12px; line-height:1.55; }
.rpt-bar { width:186mm; margin:16px auto 0; display:flex; justify-content:flex-end; }
.rpt-bar button { border:0; border-radius:8px; padding:9px 14px; background:#191F28; color:#fff; font:inherit; font-size:13px; cursor:pointer; }
.rpt-kicker { font-size:11px; color:#4E5968; font-weight:600; }
.rpt h1 { margin:4px 0 2px; font-size:22px; letter-spacing:-.03em; color:#191F28; }
.rpt-meta { font-size:11px; color:#5F6977; }
.rpt-summary { margin:8px 0 0; padding:8px 10px; border-radius:8px; background:#F7F9FB; }
.rpt h2 { margin:10px 0 4px; font-size:13px; color:#191F28; }
.rpt .rd-map { width:100%; height:auto; }
.rpt-two { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.rpt-topic { border:1px solid #E5E8EB; border-radius:10px; padding:10px 12px; }
.rpt-topic.is-fix { background:#F1EAF5; border-color:#DCCDE5; } .rpt-topic.is-keep { background:#DFEFFC; border-color:#BFE0FA; }
.rpt-tag { font-size:10px; font-weight:800; color:#fff; background:#1A73C4; border-radius:999px; padding:1px 7px; }
.is-fix .rpt-tag { background:#74498C; }
.rpt-topic h3 { margin:4px 0 0; font-size:16px; color:#191F28; }
.rpt-num { margin:2px 0 6px; font-weight:800; font-size:14px; } .rpt-num small { font-weight:500; font-size:11px; color:#4E5968; }
.rpt-topic p { margin:3px 0; } .rpt-topic p b { color:#191F28; margin-right:6px; }
.rpt blockquote { margin:6px 0 0; padding:6px 8px; border-radius:6px; background:rgba(255,255,255,.7); color:#4E5968; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.rpt-lines { margin:0; padding:0; list-style:none; display:grid; gap:4px; }
.rpt-lines li { display:grid; grid-template-columns:84px 1fr; gap:8px; }
.rpt-lines span { font-weight:700; color:#4E5968; }
.rpt-lines b { color:#74498C; }
.rpt h2 small { font-weight:500; color:#5F6977; font-size:11px; margin-left:4px; }
.rpt-design li { grid-template-columns:110px 1fr; }
.rpt-foot { margin-top:12px; padding-top:8px; border-top:1px solid #E5E8EB; font-size:10px; color:#5F6977; }
@media print { body { background:#fff; } .rpt { margin:0; width:auto; min-height:0; padding:0; font-size:11px; } .rpt-bar { display:none; } .rpt, .rpt-two, .rpt-mini { break-inside:avoid; } }
</style></head><body>
<div class="rpt-bar"><button onclick="print()">인쇄 / PDF로 저장</button></div>
<div class="page active rpt" data-page="overview">
  <div class="rpt-kicker">${esc(game)} · Steam 리뷰 진단 보고서${data.generated_at ? ` · ${esc(data.generated_at)}` : ''}</div>
  <h1>${esc(game)} 리뷰 진단</h1>
  <div class="rpt-meta">${esc(period)} · AI 분석 ${num(counts.analyzed)}건 / 수집 ${num(counts.collected)}건</div>
  ${data.summary ? `<p class="rpt-summary">${esc(data.summary)}</p>` : ''}
  <h2>IPA 매트릭스</h2>${mapSVG(700, 280)}
  <div class="rpt-two">${topicBox(s, 'keep')}${topicBox(c, 'fix')}</div>
  ${deep.length ? `<h2>심층 분석에서 찾은 것</h2><ul class="rpt-lines">${deep.map(([k, t]) => `<li><span>${k}</span><p style="margin:0">${t}</p></li>`).join('')}</ul>` : ''}
  ${design.length ? `<h2>분석 방법 <small>누가 정했나</small></h2><ul class="rpt-lines rpt-design">${design.map(r => `<li><span>${esc(r.step)} · ${esc(r.who)}</span><p style="margin:0">${esc(r.text)}${r.details?.length ? ` — ${r.details.slice(0, 3).map(esc).join(' / ')}` : ''}</p></li>`).join('')}</ul>` : ''}
  <p class="rpt-foot">${scope(data).text} 주제·감성은 AI 분류이며 한 리뷰가 여러 주제에 들어갈 수 있습니다. 칭찬 최다·불만 최다 주제는 살펴보기 시작할 곳이며, 무엇을 고칠지는 사람이 판단합니다.</p>
</div></body></html>`;
  }
  function report() {
    const html = reportHTML();
    const w = html && window.open('', '_blank');
    if (!w) return;
    w.document.open(); w.document.write(html); w.document.close();
  }

  // 카드의 보조 설명(.rd-note, .rp-note)은 숨기고 카드 머리의 ⓘ에 모은다. 내용은 그대로 두고 보이는 층만 줄인다.
  // 분석 방법 화면은 설명 자체가 내용이라 접지 않는다. 카드가 다시 그려져도 맞도록 바뀔 때마다 다시 접는다.
  const NOTE_ROOTS = ['ovBody', 'tpBody', 'chBody', 'rpDeep', 'rpReviews'];
  let noteObserver, noteTimer;
  function foldNotes() {
    noteObserver?.disconnect();
    NOTE_ROOTS.map(id => document.getElementById(id)).filter(Boolean).forEach(root => {
      root.querySelectorAll('.rd-pane, .rp-card').forEach(card => {
        const head = card.querySelector(':scope > .rd-pane-head, :scope > .rp-card-head');
        const notes = [...card.querySelectorAll('.rd-note, .rp-note')].filter(n => n.closest('.rd-pane, .rp-card') === card && n.textContent.trim());
        let tip = head?.querySelector(':scope > .rd-info');
        if (!head || !notes.length) { tip?.remove(); card.classList.remove('has-info'); return; }
        if (!tip) {
          tip = document.createElement('span');
          tip.className = 'rd-info';
          tip.innerHTML = `<button type="button" class="rd-info-btn" aria-label="설명 보기" aria-expanded="false">${icon('info')}</button><span class="rd-info-pop" role="tooltip"></span>`;
          head.appendChild(tip);
        }
        tip.querySelector('.rd-info-pop').innerHTML = notes.map(n => `<span>${n.innerHTML}</span>`).join('');
        card.classList.add('has-info');
      });
    });
    observeNotes();
  }
  function observeNotes() {
    if (!window.MutationObserver) return;
    noteObserver ??= new MutationObserver(() => { clearTimeout(noteTimer); noteTimer = setTimeout(foldNotes, 30); });
    NOTE_ROOTS.map(id => document.getElementById(id)).filter(Boolean).forEach(root => noteObserver.observe(root, {childList: true, subtree: true}));
  }
  // 손가락으로 누를 때도 열리고 닫히게
  document.addEventListener('click', event => {
    const button = event.target.closest('.rd-info-btn');
    document.querySelectorAll('.rd-info.is-open').forEach(t => { if (!button || !t.contains(button)) { t.classList.remove('is-open'); t.querySelector('.rd-info-btn')?.setAttribute('aria-expanded', 'false'); } });
    if (button) { const t = button.parentElement, open = !t.classList.contains('is-open'); t.classList.toggle('is-open', open); button.setAttribute('aria-expanded', String(open)); }
  });

  return {render, refresh, report, reportHTML, icon, plain, cohorts: renderCohorts, foldNotes, openTopic};
})();
