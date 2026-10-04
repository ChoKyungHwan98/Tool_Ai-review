/* 불만 분석 · 리뷰 원문 · 분석 방법 화면. 데이터는 /dashboard/data/v5 응답(DATA)만 쓴다.
   숫자 카드 줄은 그 화면에만 있는 숫자가 있을 때만 둔다(분석 방법). 아래 카드에 이미 나오는 숫자를 위에서 되풀이하지 않는다. */
window.ReviewPages = (() => {
  const LANG_NAMES = {koreana:'한국어', english:'영어', japanese:'일본어', schinese:'중국어 간체', tchinese:'중국어 번체', russian:'러시아어', spanish:'스페인어', latam:'스페인어(중남미)', brazilian:'포르투갈어(브라질)', german:'독일어', french:'프랑스어', polish:'폴란드어', turkish:'튀르키예어', thai:'태국어', vietnamese:'베트남어', all:'모든 언어', unknown:'언어 미상'};
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = x => x == null || x === '' ? '—' : Number(x).toLocaleString('ko-KR');
  const pct = x => x == null ? '—' : `${Number(x).toFixed(1)}%`;
  const share = (a, b) => b ? `${Math.round(a / b * 100)}%` : '';
  // 아이콘은 진단 요약 화면의 것을 같이 쓴다
  const icon = name => window.ReviewDashboard?.icon?.(name) || '';
  // 숫자 카드: 이름 · 오른쪽 위 아이콘 · 값 + 옆의 작은 보조 수치 · 설명. tag를 button으로 주면 누를 수 있는 카드가 된다.
  const kpi = (name, iconName, value, unit, side, caption, tag = 'div', attrs = '', tone = '') => `<${tag} class="rd-kpi ${tone}" ${attrs}>
      ${icon(iconName)}
      <div class="rd-kpi-name">${name}</div>
      <div class="rd-kpi-value"><b>${value}</b>${unit}${side ? `<span>${side}</span>` : ''}</div>
      <small>${caption}</small>
    </${tag}>`;
  // 카드 제목 줄: 제목 + 설명, 오른쪽에 아이콘
  const cardHead = (title, sub, iconName, side = '') => `<header class="rp-card-head"><div><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ''}</div>${side}${iconName ? icon(iconName) : ''}</header>`;
  const MOODS = [
    {key:'POSITIVE', label:'긍정', cls:'pos', icon:'up', desc:'좋았다는 글'},
    {key:'MIXED', label:'혼합', cls:'mix', icon:'scale', desc:'좋은 점과 아쉬운 점이 함께'},
    {key:'NEGATIVE', label:'부정', cls:'neg', icon:'down', desc:'아쉬웠다는 글'},
    {key:'NEUTRAL', label:'판단 어려움', cls:'unk', icon:'info', desc:'반응을 가리기 어려운 글'}];
  const PAGE = 40;

  /* ---------------- 리뷰 원문 ---------------- */
  let rows = [], topics = [], filter, sort, shown, root;

  const topicsOf = r => String(r.keywords || '').split('|').filter(Boolean);
  const moodOf = r => MOODS.some(m => m.key === r.overall_sentiment) ? r.overall_sentiment : 'NEUTRAL';
  const isUp = r => !['0', 'false', 'False'].includes(String(r.voted_up));
  // Steam 리뷰의 꾸밈 표시([h1], [b], [list], [*] 등)를 걷어 내고 읽을 글만 남긴다.
  // 수집할 때 줄바꿈이 빈칸으로 바뀌었으므로, 빈칸이 이어진 곳을 줄바꿈으로 되돌린다(네 칸 이상은 문단 사이).
  const readable = text => String(text || '')
    .replace(/\[\*\]/g, '\n· ').replace(/\[\/?(h[1-3]|list|olist|quote|code|table|tr|hr)[^\]]*\]/gi, '\n')
    .replace(/\[\/?[a-z0-9]+(=[^\]]*)?\]/gi, '')
    .replace(/[ \t]{4,}/g, '\n\n').replace(/[ \t]{2,3}/g, '\n')
    .replace(/[ \t]*\n[ \t]*/g, '\n').replace(/\n{3,}/g, '\n\n').trim();

  function renderReviews(data) {
    root = document.getElementById('rpReviews');
    if (!root) return;
    rows = (data?.reviews || []).map(r => ({...r, _text: readable(r.content), _topics: topicsOf(r), _mood: moodOf(r), _up: isUp(r)}));
    const themes = data?.evidence?.themes || [];
    topics = [...themes].sort((a, b) => b.mentions - a.mentions).slice(0, 14);
    filter = {mood: '', vote: '', topic: '', q: ''};
    sort = '';
    shown = PAGE;
    if (!rows.length) { root.innerHTML = '<div class="rp-card rp-empty">분석된 리뷰가 아직 없습니다.</div>'; return; }
    root.innerHTML = `
      <div class="rd-kpis" id="rpMoodCards" aria-label="반응별 리뷰 선택"></div>
      <div class="rp-grid">
        <aside class="rp-card rp-side">
          ${cardHead('걸러 보기', '조건을 고르면 오른쪽 목록이 바뀝니다', 'info')}
          <label class="rp-field"><span>리뷰 내용 검색</span><input class="rp-search" id="rpSearch" type="search" placeholder="낱말을 입력하세요" /></label>
          <div class="rp-field"><span>Steam 추천 여부</span><div class="rp-toggle" id="rpVote"><button data-vote="">전체</button><button data-vote="up">추천</button><button data-vote="down">비추천</button></div></div>
          <div class="rp-field"><span>정렬</span><div class="rp-toggle" id="rpSort"><button data-sort="">최신</button><button data-sort="helpful">도움됨</button><button data-sort="hours">플레이 시간</button></div></div>
          <div class="rp-field" ${topics.length ? '' : 'hidden'}><span>주제</span><div class="rp-chips" id="rpTopics"></div></div>
          <button class="rp-text-btn" id="rpReset">필터 초기화</button>
        </aside>
        <section class="rp-card rp-main">
          ${cardHead('리뷰', '<span id="rpCount"></span>', 'review')}
          <div class="rp-list" id="rpList"></div>
        </section>
      </div>`;
    root.onclick = onReviewsClick;
    document.getElementById('rpSearch').addEventListener('input', e => { filter.q = e.target.value.trim().toLowerCase(); shown = PAGE; draw(); });
    draw();
  }

  function matches(r, skip) {
    if (skip !== 'mood' && filter.mood && r._mood !== filter.mood) return false;
    if (skip !== 'vote' && filter.vote && (filter.vote === 'up') !== r._up) return false;
    if (skip !== 'topic' && filter.topic && !r._topics.includes(filter.topic)) return false;
    if (filter.q && !`${r.content} ${r.key_phrase || ''}`.toLowerCase().includes(filter.q)) return false;
    return true;
  }

  function draw() {
    // 반응 칩: 다른 필터를 적용한 뒤의 건수. 누르면 그 반응만 본다.
    const base = rows.filter(r => matches(r, 'mood'));
    const total = base.length || 1;
    document.getElementById('rpMoodCards').innerHTML = MOODS.map(m => {
      const n = base.filter(r => r._mood === m.key).length;
      return `<button type="button" class="rd-kpi is-${m.cls}" data-mood="${m.key}" aria-pressed="${filter.mood === m.key}" aria-label="${m.label} ${num(n)}건 (${pct(n / total * 100)}), 누르면 이 반응만 봅니다">${icon(m.icon)}<span class="rd-kpi-name">${m.label}</span><span class="rd-kpi-value"><b>${num(n)}</b>건<span>${pct(n / total * 100)}</span></span><small>${m.desc}</small></button>`;
    }).join('');
    // 주제는 이름으로 고른다. 불만 비율은 진단 차트에서 확인한다.
    const forTopics = rows.filter(r => matches(r, 'topic'));
    document.getElementById('rpTopics').innerHTML = topics.map(t => {
      const n = forTopics.filter(r => r._topics.includes(t.name)).length;
      return `<button class="rp-chip" data-topic="${esc(t.name)}" aria-pressed="${filter.topic === t.name}" title="${esc(t.name)} · 칭찬 ${num(t.pos)} · 불만 ${num(t.neg)}">${esc(t.name)}<small>${num(n)}</small></button>`;
    }).join('');
    root.querySelectorAll('#rpVote button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.vote === filter.vote)));
    root.querySelectorAll('#rpSort button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sort === sort)));
    // 정렬: 기본은 서버가 준 순서(최신순으로 수집). 고르면 큰 값부터, 값이 없는 리뷰는 뒤로 보낸다.
    const key = {helpful: r => Number(r.helpful) || 0, hours: r => r.playtime_h === '' || r.playtime_h == null ? -1 : Number(r.playtime_h)}[sort];
    const list = rows.filter(r => matches(r));
    if (key) list.sort((a, b) => key(b) - key(a));
    const active = filter.mood || filter.vote || filter.topic || filter.q;
    document.getElementById('rpCount').innerHTML = active ? `조건에 맞는 리뷰 <b>${num(list.length)}건</b> / 전체 ${num(rows.length)}건` : `AI가 분석한 리뷰 <b>${num(rows.length)}건</b>`;
    document.getElementById('rpReset').hidden = !active;
    const moodLabel = Object.fromEntries(MOODS.map(m => [m.key, m]));
    document.getElementById('rpList').innerHTML = list.slice(0, shown).map(r => {
      const m = moodLabel[r._mood];
      const long = r._text.length > 180 || r._text.split('\n').length > 3;
      return `<article class="rp-review ${m.cls}">
        <header>
          <span class="rp-mood ${m.cls}">${m.label}</span>
          <span class="rp-vote ${r._up ? 'up' : 'down'}">${r._up ? '추천' : '비추천'}</span>
          ${r.language ? `<span class="rp-lang">${esc(LANG_NAMES[r.language] || r.language)}</span>` : ''}
          <span class="rp-meta">${[r.playtime_h === '' || r.playtime_h == null ? '플레이 시간 미상' : `${num(Math.round(Number(r.playtime_h)))}시간 플레이`,
            Number(r.helpful) ? `도움됨 ${num(r.helpful)}` : '',
            Number(r.created) ? new Date(Number(r.created) * 1000).toISOString().slice(0, 10).replaceAll('-', '.') : ''].filter(Boolean).join('<i>·</i>')}</span>
          <span class="rp-tags">${r._topics.map(t => `<em class="${t === filter.topic ? 'on' : ''}">${esc(t)}</em>`).join('')}</span>
        </header>
        ${r.key_phrase ? `<strong>${esc(r.key_phrase)}</strong>` : ''}
        <p class="${long ? 'is-clamped' : ''}">${esc(r._text)}</p>
        ${long ? '<button class="rp-text-btn" data-more>전체 보기</button>' : ''}
      </article>`;
    }).join('') + (list.length > shown ? `<button class="rp-more" data-page>리뷰 ${num(Math.min(PAGE, list.length - shown))}건 더 보기</button>` : '')
      + (list.length ? '' : '<div class="rp-empty">조건에 맞는 리뷰가 없습니다.</div>');
  }

  function onReviewsClick(e) {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.mood) filter.mood = filter.mood === b.dataset.mood ? '' : b.dataset.mood;
    else if (b.dataset.topic) filter.topic = filter.topic === b.dataset.topic ? '' : b.dataset.topic;
    else if (b.dataset.vote !== undefined) filter.vote = b.dataset.vote;
    else if (b.dataset.sort !== undefined) sort = b.dataset.sort;
    else if (b.id === 'rpReset') { filter = {mood: '', vote: '', topic: '', q: ''}; document.getElementById('rpSearch').value = ''; }
    else if (b.hasAttribute('data-page')) { shown += PAGE; draw(); return; }
    else if (b.hasAttribute('data-more')) { const p = b.previousElementSibling; p.classList.toggle('is-clamped'); b.textContent = p.classList.contains('is-clamped') ? '전체 보기' : '접기'; return; }
    else return;
    shown = PAGE; draw();
  }

  /* ---------------- 분석 방법 ---------------- */
  const SCORE_NAMES = {'Overwhelmingly Positive':'압도적으로 긍정적', 'Very Positive':'매우 긍정적', 'Positive':'긍정적', 'Mostly Positive':'대체로 긍정적', 'Mixed':'복합적',
    'Mostly Negative':'대체로 부정적', 'Negative':'부정적', 'Very Negative':'매우 부정적', 'Overwhelmingly Negative':'압도적으로 부정적'};
  function renderSample(data) {
    const box = document.getElementById('rpSample');
    if (!box) return;
    const sd = data?.sample_design_full || {};
    const pop = sd.population || {}, params = sd.params || {};
    const counts = data?.evidence?.counts || {};
    const collected = counts.collected ?? sd.actual?.total;
    const colNeg = counts.negative ?? sd.actual?.negative;
    if (!collected) { box.innerHTML = '<div class="rp-card rp-empty">수집 기록이 아직 없습니다.</div>'; return; }
    const colNegRate = colNeg != null ? colNeg / collected * 100 : null;
    const popNegRate = pop.total ? pop.negative / pop.total * 100 : null;
    const sortLabel = params.sort === 'helpful' ? '공감순' : params.sort === 'random' ? '무작위' : '최신순';
    const pool = params.random_pool, random = params.sort === 'random';
    const since = params.since ? params.since.replaceAll('-', '.') : '';
    const langs = data?.evidence?.languages || [];
    const promise = data?.evidence?.promise;
    // 추천과 비추천을 모은 기간이 크게 어긋나면(최신순 할당 수집) 둘을 같은 시기의 의견으로 견줄 수 없다
    const vp = data?.evidence?.vote_periods, gap = data?.evidence?.vote_period_gap_days;
    const gapNote = vp?.up && vp?.down && gap != null && gap > Math.max(7, Math.max(vp.up.days, vp.down.days, 1) * .2)
      ? ` 추천과 비추천을 모은 기간이 ${num(gap)}일 어긋납니다.` : '';
    // 추천했는데 글은 부정으로, 비추천인데 글은 긍정으로 분류한 리뷰. AI가 얼마나 틀렸을지 짐작하게 하는 유일한 숫자다.
    const mood = counts.vote_mood, clash = mood ? (mood.up?.N || 0) + (mood.down?.P || 0) : null;
    const u = data?.usage || {}, stand = Object.entries(u.stand_ins || {}).sort((x, y) => y[1] - x[1]);
    const calls = ['A', 'B', 'C', 'D'].reduce((n, k) => n + (u[k]?.calls || 0), 0), standCalls = stand.reduce((n, [, v]) => n + v, 0);
    const short = m => String(m || '').split('/').pop().replace(':free', '');
    // 내부 처리 다섯 단계. 지시문을 다 적지 않고, 단계마다 무엇을 했는지 한 문장으로만 적는다.
    const lang = LANG_NAMES[params.language || data?.evidence?.language] || '';
    const analyzedN = counts.analyzed, shortN = counts.short_excluded ?? 0;
    const steps = [
      ['리뷰 모으기', '규칙', random ? `Steam ${esc(lang)} 리뷰 ${num(pop.total || pool?.size)}건을 ${pool?.complete ? '끝까지 ' : ''}훑고, 그중 ${num(collected)}건을 무작위로 뽑았습니다.` : `Steam ${esc(lang)} 리뷰를 ${sortLabel}으로 ${num(collected)}건 모았습니다.`],
      ['주제 찾기', 'AI', `리뷰 일부를 읽고 이 게임에서 자주 나오는 주제 ${num(data?.evidence?.n_ai_topics)}개를 정했습니다.`],
      ['리뷰 분류', 'AI', `${num(analyzedN)}건을 하나씩 읽고 긍정·부정과 주제를 붙였습니다.${shortN ? ` 한 글자짜리 ${num(shortN)}건은 읽지 않았습니다.` : ''}`],
      ['불만 읽기', 'AI', '불만이 담긴 리뷰를 다시 읽고 무슨 문제인지, 왜 그런지, 무엇을 바라는지 적었습니다.'],
      ['세기와 요약', '규칙', '건수와 비율은 프로그램이 셉니다. 주제별 요약 문장만 AI가 썼습니다.'],
    ];
    // 맨 윗줄은 다른 화면과 같은 숫자 카드다. 이름 자리에 질문을, 값 자리에 답을 둔다.
    box.innerHTML = `
      <div class="rd-kpis">
        ${kpi('몇 건을 어떻게 봤나', 'review', num(collected), '건', '',
          random && pool?.complete ? `${since ? `${since} 이후 ` : ''}${num(pop.total || pool.size)}건을 끝까지 훑고 무작위로 뽑음`
            : random ? '무작위로 뽑았지만 끝까지 훑지는 못함' : `${sortLabel}으로 수집 · 무작위 표본이 아님`)}
        ${kpi('추천 비율은 믿을 만한가', 'scale', promise?.kept ? '예' : '말하기 어려움', '', promise?.kept ? `오차범위 ±${num(promise.analyzed_margin)}%` : '',
          promise && !promise.kept ? esc((promise.checks.find(c => !c.ok) || {}).text || '무작위로 끝까지 훑어 뽑지 않았습니다')
            : (popNegRate != null && colNegRate != null ? `비추천 · Steam 전체 ${pct(popNegRate)} · 수집한 리뷰 ${pct(colNegRate)}` : 'Steam 전체와 견줄 기록이 없음') + (gapNote ? ' · 수집 기간이 어긋남' : ''), 'div', gapNote ? `title="${gapNote.trim()}"` : '', promise?.kept ? '' : 'is-neg')}
        ${kpi('주제별 숫자는 믿을 만한가', 'info', '원문 확인 필요', '', '',
          `AI 분류${clash ? ` · 추천과 반대로 분류한 글 ${num(clash)}건` : ''}`, 'div', counts.themed != null ? `title="주제가 붙은 ${num(counts.themed)}건에서 나온 숫자입니다"` : '', 'is-neg')}
        ${u.model ? kpi('어느 AI가 읽었나', 'spark', standCalls ? '다른 모델이 대신 답함' : '고른 모델 그대로', '', '',
          standCalls ? `${esc(short(u.model))} 대신 ${esc(short(stand[0][0]))} · ${num(calls)}번 중 ${num(standCalls)}번` : `${esc(short(u.model))} · ${num(calls)}번`, 'div', `title="${esc(u.model)}"`, standCalls ? 'is-neg' : '') : ''}
      </div>
      <section class="rp-card rp-how">
        ${cardHead('어떻게 분석했나', '왼쪽에서 오른쪽 순서로 진행합니다. 색 표시는 그 일을 누가 했는지입니다', '')}
        <ol class="rp-flow">${steps.map(([name, who, text]) => `<li><span class="rp-who ${who === 'AI' ? 'ai' : 'rule'}">${who}</span><b>${name}</b><p>${text}</p></li>`).join('')}</ol>
      </section>
      ${biasHTML(data?.evidence?.bias)}`;
  }

  // 치우침 점검: 수집 → 분석 → 주제로 걸러질 때마다 남은 글의 성격이 달라지는지 잰다. 사람도 AI도 쓰지 않는다.
  function biasHTML(rows) {
    rows = (rows || []).filter(r => r && r.n);
    const base = rows[0];
    if (!base || rows.length < 2) return '';
    const off = (r, key, limit) => r !== base && r[key] != null && base[key] != null && Math.abs(r[key] - base[key]) >= limit(base[key]);
    const cell = (r, key, text, limit) => `<td class="${off(r, key, limit) ? 'is-cross' : ''}">${r[key] == null ? '—' : text(r[key])}</td>`;
    const themed = rows.find(r => r.key === 'themed') || rows[1];
    const gap = themed.negative_rate - base.negative_rate, hours = themed.hours != null && base.hours != null ? themed.hours - base.hours : null;
    return `<section class="rp-card">
        ${cardHead('주제가 붙은 글은 전체와 얼마나 다른가', '주제별 숫자는 주제가 붙은 글에서만 나옵니다 · 색칠한 칸은 전체와 차이가 큰 값 · 플레이 시간과 글 길이는 가운데 값', 'scale')}
        <table class="rd-cross"><thead><tr><td></td><th scope="col">비추천</th><th scope="col">플레이 시간</th><th scope="col">글 길이</th></tr></thead>
          <tbody>${rows.map(r => `<tr><th scope="row">${esc(r.name)}<small>${num(r.n)}건</small></th>
            ${cell(r, 'negative_rate', pct, () => 2)}${cell(r, 'hours', v => `${num(Math.round(v))}시간`, v => Math.max(5, v * .25))}${cell(r, 'length', v => `${num(v)}자`, v => Math.max(5, v * .5))}</tr>`).join('')}</tbody></table>
        <p class="rp-callout">주제 숫자가 나오는 <b>${esc(themed.name)} ${num(themed.n)}건</b>은 수집한 리뷰 전체보다 비추천이 <b>${Math.abs(gap).toFixed(1)}%p</b> ${hours == null ? (gap >= 0 ? '많습니다' : '적습니다') : `${gap >= 0 ? '많고' : '적고'}, 플레이 시간이 <b>${num(Math.abs(Math.round(hours)))}시간</b> ${hours >= 0 ? '깁니다' : '짧습니다'}`}.</p>
      </section>`;
  }

  /* ---------------- 불만 분석 ---------------- */
  // 한 화면에 들어온다. 왼쪽에서 주제를 고르면 오른쪽만 바뀐다. 접거나 아래로 늘어놓지 않는다.
  const MIN_PROBLEM = 5;   // 불만이 이보다 적은 주제는 리뷰 한두 건의 말이라 목록에 올리지 않는다
  let deepTheme;
  function renderDeep(data) {
    const box = document.getElementById('rpDeep');
    if (!box) return;
    const deep = data?.evidence?.deep;
    if (!deep) { box.innerHTML = '<div class="rp-card rp-empty">분석 결과가 아직 없습니다.</div>'; return; }
    const ev = data.evidence, counts = ev.counts || {};
    const rows = [...(ev.themes || [])].filter(t => t.neg >= MIN_PROBLEM).sort((a, b) => b.neg - a.neg || a.name.localeCompare(b.name));
    const worst = (ev.cohorts || []).filter(c => !c.small && c.negative_rate != null).sort((a, b) => b.negative_rate - a.negative_rate)[0];
    if (!rows.some(t => t.name === deepTheme)) deepTheme = rows[0]?.name;
    box.innerHTML = `
      <div class="rd-kpis">
        ${counts.complaint_reviews != null ? kpi('불만이 담긴 리뷰', 'down', num(counts.complaint_reviews), '건', share(counts.complaint_reviews, counts.analyzed), `AI 분석 ${num(counts.analyzed)}건 중`, 'div', '', 'is-neg') : ''}
        ${counts.recommended_complaints != null ? kpi('불만을 쓰고도 추천', 'up', num(counts.recommended_complaints), '건', share(counts.recommended_complaints, counts.complaint_reviews), '불만이 담긴 리뷰 중', 'div', '', '') : ''}
        ${rows[0] ? kpi('불만이 가장 많은 주제', 'scale', esc(rows[0].name), '', '', `불만 ${num(rows[0].neg)}건 · 칭찬 ${num(rows[0].pos)}건`, 'div', '', 'is-neg') : ''}
        ${worst ? kpi('비추천율이 가장 높은 구간', 'clock', esc(worst.label), '', pct(worst.negative_rate), `${num(worst.n)}건 중 비추천 ${num(worst.negative)}건`, 'div', '', 'is-neg') : ''}
      </div>
      ${rows.length ? `<div class="rp-grid rp-problems">
        <section class="rp-card rp-problem-list" id="rpProblemList"></section>
        <div class="rp-pstack" id="rpProblem"></div>
      </div>` : `<div class="rp-card rp-empty">불만이 ${MIN_PROBLEM}건 이상 모인 주제가 없습니다.</div>`}`;
    if (!rows.length) return;
    const draw = () => { drawProblemList(rows, counts); drawProblem(data, rows.find(t => t.name === deepTheme)); };
    box.onclick = e => {
      const pick = e.target.closest('[data-problem]'), open = e.target.closest('[data-reviews]');
      if (pick) { deepTheme = pick.dataset.problem; draw(); }
      else if (open) window.ReviewDashboard?.openTopic?.(open.dataset.reviews, 'N');
    };
    draw();
  }

  function drawProblemList(rows, counts) {
    const max = rows[0].neg;
    document.getElementById('rpProblemList').innerHTML = cardHead('불만 주제', '불만이 많은 순서 · 누르면 오른쪽이 바뀝니다', 'down')
      + `<div class="rp-plist">${rows.map(t => `<button type="button" class="rp-pitem" data-problem="${esc(t.name)}" aria-pressed="${t.name === deepTheme}"><b>${esc(t.name)}</b><span class="rp-ptrk"><i style="width:${t.neg / max * 100}%"></i></span><em>${num(t.neg)}건</em></button>`).join('')}</div>`
      + (counts.off_list_complaints ? `<p class="rp-poff">주제에 들지 않은 불만 리뷰가 ${num(counts.off_list_complaints)}건 있습니다.</p>` : '');
  }

  // 고른 주제 하나: 무슨 일인가 · 원인 · 바라는 것 / 언제 나오나 · 실제 리뷰.
  // AI 요약(data.actions)이 있으면 그 문장을, 없으면 리뷰마다 적어 둔 불만 메모를 보여 준다. 바라는 것은 리뷰에 적힌 요청 그대로다.
  function drawProblem(data, t) {
    const ev = data.evidence, deep = ev.deep, plain = x => window.ReviewDashboard?.plain?.(x) ?? x;
    const act = data.actions?.find(x => x.theme === t.name) || {};
    const notes = deep.notes?.[t.name] || [];
    const wants = (deep.wants || []).filter(w => w.theme === t.name).slice(0, 2);
    // 주제를 바꿔도 화면 높이가 달라지지 않도록 항목마다 두 줄까지만 보여 준다. 전체 문장은 마우스를 올리면 보인다.
    const list = items => `<ul>${items.map(x => `<li><span class="rp-clamp" title="${x.replace(/<[^>]+>/g, '')}">${x}</span></li>`).join('')}</ul>`;
    const whys = notes.filter(n => n.why).slice(0, 2);
    const cells = (t.cells || []).map((c, i) => ({...c, label: ev.cohorts?.[i]?.label || ''}));
    const quotes = (t.examples?.N || []).slice(0, 3);
    const summed = Boolean(act.prob || act.why);
    const card = (cls, title, sub, body, note, extra = '') => `<section class="rp-card ${cls}">${cardHead(title, sub, '', extra)}${body}${note ? `<p class="rp-note">${note}</p>` : ''}</section>`;
    // 읽는 순서: 무슨 일인가 → 왜 그런가 → 무엇을 바라나, 그다음 언제 나오나와 실제 리뷰
    document.getElementById('rpProblem').innerHTML = `
      <h2 class="rp-ptitle">${esc(t.name)}<small>불만 ${num(t.neg)}건${t.negative_recommended != null ? ` · 그중 ${num(t.negative_recommended)}건은 그래도 게임을 추천했습니다` : ''}</small></h2>
      <div class="rp-pthree">
        ${card('', '무슨 일인가', summed ? 'AI가 요약한 문장' : '리뷰마다 AI가 적어 둔 메모',
          act.prob ? `<p>${esc(act.prob)}</p>` : notes.length ? list(notes.slice(0, 3).map(n => esc(n.prob))) : '<p class="is-none">불만 메모가 없습니다.</p>',
          summed ? 'AI가 이 주제의 불만 리뷰를 요약한 문장입니다. 원문으로 확인해 주십시오.' : 'AI가 리뷰마다 적어 둔 메모이고, 도움됨이 많은 리뷰부터 보여 줍니다. AI가 주제를 잘못 붙였을 수 있으니 원문으로 확인해 주십시오.')}
        ${card('', '왜 그런가', summed && act.why ? 'AI가 요약한 문장' : '리뷰에 적힌 문장',
          act.why ? `<p>${esc(act.why)}</p>` : whys.length ? list(whys.map(n => `“${esc(n.why)}”`)) : '<p class="is-none">이유를 적은 리뷰가 없습니다.</p>')}
        ${card('', '무엇을 바라나', '리뷰에 적힌 요청 그대로',
          wants.length ? list(wants.map(w => `“${esc(w.text)}” <span>${w.count > 1 ? `${num(w.count)}건 · ` : ''}도움됨 ${num(w.votes)}</span>`)) : '<p class="is-none">구체적으로 요청한 리뷰가 없습니다.</p>')}
      </div>
      <div class="rp-ptwo">
        ${card('', '언제 나오나', '플레이 시간별로, 리뷰 100건 중 이 불만을 말한 건수', window.ReviewDashboard?.whenHTML?.(cells) || '',
          '그 구간의 AI 분석 리뷰 중 이 주제를 불만으로 말한 리뷰의 비율입니다. 리뷰가 30건이 안 되는 구간은 표시하지 않습니다.')}
        ${card('', '실제 리뷰', '이 불만이 붙은 리뷰',
          quotes.length ? quotes.map(q => `<blockquote><span>${esc(plain(q.content))}${q.truncated ? '…' : ''}</span></blockquote><p class="rp-pwho">${q.recommended ? '게임 추천' : '게임 비추천'}${q.hours == null ? '' : ` · ${num(Math.round(q.hours))}시간 플레이`}${q.helpful ? ` · 도움됨 ${num(q.helpful)}` : ''}</p>`).join('') : '<p class="is-none">보여 줄 리뷰가 없습니다.</p>',
          '', `<button type="button" class="rd-btn" data-reviews="${esc(t.name)}">${num(t.neg)}건 모두 보기</button>`)}
      </div>`;
  }

  // 카드 머리의 한 줄 인사이트. 한 장 보고서도 같은 문장을 쓴다.
  const churnGap = t => (t.early_share ?? 0) - (t.later_share ?? 0);
  // 한 줄 문장으로 말할 만큼 건수가 있는가. 두 묶음이 각각 30건, 그 주제가 5건은 되어야 리뷰 한두 개로 뒤집히지 않는다.
  const churnEnough = (t, c) => t.early >= 5 && c.early_n >= 30 && c.later_n >= 30;
  const agreeGap = t => (t.vote_share || 0) - (t.count_share || 0);
  // 도움됨은 리뷰 하나가 수십 표를 몰아 받는다. 전체 50표가 안 되거나, 그 주제에서 표를 받은 리뷰가 5건이 안 되면 비율을 견주지 않는다.
  const agreeEnough = (t, a) => a.total_votes >= 50 && t.votes >= 10 && t.voted >= 5;
  const churnPick = deep => [...deep.churn.topics].filter(t => t.early >= 2).sort((a, b) => churnGap(b) - churnGap(a))[0];
  const agreePick = deep => [...deep.agreed.topics].sort((a, b) => agreeGap(b) - agreeGap(a))[0];
  const lines = {
    churn: deep => {
      const p = churnPick(deep), h = deep.early_hours;
      if (!deep.churn.early_n) return '';
      return p && churnGap(p) > 5 && churnEnough(p, deep.churn)
        ? `수집한 비추천 리뷰에서 <b>'${esc(p.name)}'</b> 불만은 ${h}시간 미만에 더 자주 적혔습니다 (${num(p.early)}/${num(deep.churn.early_n)}건 vs ${num(p.later)}/${num(deep.churn.later_n)}건).`
        : p && churnGap(p) > 5 ? `${h}시간 전후의 차이를 살피기에는 수집한 리뷰가 적습니다.`
        : `수집한 비추천 리뷰에서 ${h}시간 전후의 불만 비율 차이는 5%p 이하입니다.`;
    },
    agreed: deep => {
      const p = agreePick(deep);
      if (!deep.agreed.total_votes) return '';
      return p && agreeGap(p) > 5 && agreeEnough(p, deep.agreed) ? `<b>'${esc(p.name)}'</b> 불만은 건수로는 ${pct(p.count_share)}지만, Steam 도움됨은 ${pct(p.vote_share)}를 받았습니다 (${num(p.votes)}/${num(deep.agreed.total_votes)}표).`
        : p && agreeGap(p) > 5 ? `도움됨을 받은 불만 리뷰가 적어 주제끼리 견주기 어렵습니다 (모두 ${num(deep.agreed.total_votes)}표).`
        : 'Steam 도움됨은 불만 건수와 비슷하게 나뉘어 있습니다.';
    },
    wants: deep => !deep.wants[0] ? '' : deep.wants[0].count < 2 ? '두 리뷰 이상이 똑같이 적은 요청은 없습니다. 아래는 리뷰 하나씩의 요청입니다.' : `가장 많이 나온 요청은 <b>“${esc(deep.wants[0].text)}”</b>입니다 (${num(deep.wants[0].count)}건).`,
  };
  // 한 장 보고서용: 심층 분석 세 줄 (비어 있는 줄은 뺌)
  function deepLines(deep) {
    if (!deep) return [];
    return [[`${deep.early_hours}시간 미만 비추천`, lines.churn(deep)],
            ['Steam 도움됨', lines.agreed(deep)], ['유저 요청', deep.has_complaints ? lines.wants(deep) : '']].filter(([, t]) => t);
  }

  return {renderReviews, renderSample, renderDeep, deepLines};
})();
