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
  const cardHead = (title, sub, iconName, extra = '') => `<header class="rp-card-head"><div><h2>${title}${extra}</h2>${sub ? `<p>${sub}</p>` : ''}</div>${iconName ? icon(iconName) : ''}</header>`;
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
    const pop = sd.population || {}, design = sd.design || {}, params = sd.params || {};
    const counts = data?.evidence?.counts || {};
    const collected = counts.collected ?? sd.actual?.total;
    const analyzed = counts.analyzed;
    const colNeg = counts.negative ?? sd.actual?.negative;
    if (!collected) { box.innerHTML = '<div class="rp-card rp-empty">수집 기록이 아직 없습니다.</div>'; return; }
    const colNegRate = colNeg != null ? colNeg / collected * 100 : null;
    const popNegRate = pop.total ? pop.negative / pop.total * 100 : null;
    const skipped = analyzed != null ? collected - analyzed : null;
    const shortExcluded = counts.short_excluded ?? skipped;
    const analysisMissing = counts.analysis_missing_eligible ?? 0;
    const ratio = popNegRate && colNegRate != null ? colNegRate / popNegRate : null;
    const balanceNote = ratio == null ? ''
      : ratio >= 1.3 ? `불만을 충분히 보려고 비추천 리뷰를 Steam 전체보다 <b>${ratio.toFixed(1)}배</b> 많은 비율로 모았습니다.`
      : ratio <= .77 ? `수집한 리뷰의 비추천 비율이 Steam 전체보다 낮습니다. 최신 리뷰 중 비추천이 적었기 때문일 수 있습니다.`
      : params.sort === 'random' ? 'Steam 전체와 비슷한 비율로 모였습니다.' : '수집할 때 이 비율에 맞춰 건수를 나눴기 때문에 같습니다.';
    const bar = (label, sub, negRate) => negRate == null ? '' : `<div class="rp-bal-row">
        <span class="rp-bal-label"><b>${label}</b><small>${sub}</small></span>
        <span class="rp-bal-val">비추천 <b>${pct(negRate)}</b></span>
        <span class="rp-bal-bar" role="img" aria-label="${label}: 추천 ${pct(100 - negRate)}, 비추천 ${pct(negRate)}"><i class="up" style="width:${100 - negRate}%"></i><i class="down" style="width:${negRate}%"></i></span></div>`;
    const sortLabel = params.sort === 'helpful' ? '공감순' : params.sort === 'random' ? '무작위' : '최신순';
    const pool = params.random_pool;
    const since = params.since ? params.since.replaceAll('-', '.') : '';
    const lang = LANG_NAMES[params.language || data?.evidence?.language] || '';
    const langs = data?.evidence?.languages || [];
    const q = data?.quality_report;
    box.innerHTML = `
      <div class="rd-kpis">
        ${kpi(`Steam ${esc(lang)} 리뷰 전체`, 'globe', pop.total ? num(pop.total) : '—', pop.total ? '건' : '', '', pop.score ? `Steam 평가 · ${esc(SCORE_NAMES[pop.score] || pop.score)}` : '수집 당시 Steam에 올라온 리뷰')}
        ${kpi(`${sortLabel}으로 수집`, 'review', num(collected), '건', pop.total ? pct(collected / pop.total * 100) : '', since ? `${since} 이후 · 전체 중 비율` : 'Steam 전체 중 비율')}
        ${kpi('AI가 분석', 'spark', num(analyzed), analyzed == null ? '' : '건', analyzed == null ? '' : share(analyzed, collected), '수집한 리뷰 중 분석한 비율')}
        ${q?.overall_score != null ? kpi('자료 상태 점검', 'matrix', num(q.overall_score), '점', esc(q.grade_kr || ''), `내부 기준 ${q.thresholds?.pass ?? 80}점`) : ''}
      </div>
      <div class="rp-grid">
        <div class="rp-col rp-col-main">
          ${designLogHTML(data?.design_log)}
          <div class="rp-duo">${qualityHTML(q)}${usageHTML(data?.usage)}</div>
        </div>
        <div class="rp-col">
          ${analyzed != null ? `<section class="rp-card">
            ${cardHead('수집에서 분석까지', `수집 ${num(collected)}건 중`, 'spark')}
            <div class="rp-split" role="img" aria-label="수집 ${collected}건 중 주제가 붙은 리뷰 ${counts.themed ?? '알 수 없음'}건, 주제 없이 반응만 센 리뷰 ${counts.themed != null ? analyzed - counts.themed : '알 수 없음'}건, 짧아서 제외 ${shortExcluded}건, 분석 대상 누락 ${analysisMissing}건">
              ${counts.themed != null ? `<i class="done" style="flex:${counts.themed} 1 0"></i><i class="use-1" style="flex:${analyzed - counts.themed} 1 0"></i>` : `<i class="done" style="flex:${analyzed} 1 0"></i>`}<i class="skip" style="flex:${Math.max(skipped, 0)} 1 0"></i></div>
            <div class="rp-use-list">${counts.themed != null ? `<span><i class="done"></i>주제가 붙음 <b>${num(counts.themed)}건</b> · ${share(counts.themed, collected)}</span><span><i class="use-1"></i>주제 없이 반응만 <b>${num(analyzed - counts.themed)}건</b> · ${share(analyzed - counts.themed, collected)}</span>` : `<span><i class="done"></i>AI 분석 <b>${num(analyzed)}건</b></span>`}<span><i class="skip"></i>짧아서 제외 <b>${num(shortExcluded)}건</b> · ${share(shortExcluded, collected)}</span>${analysisMissing ? `<span><i class="skip"></i>분석 대상 누락 <b>${num(analysisMissing)}건</b> · ${share(analysisMissing, collected)}</span>` : ''}</div>
            <p class="rp-note">주제별 숫자는 주제가 붙은 글에서만 나옵니다.</p>
            ${langs.length > 1 ? `<p class="rp-note">수집한 리뷰의 언어: ${langs.map(([k, n]) => `${esc(LANG_NAMES[k] || k)} ${num(n)}건`).join(' · ')}</p>` : ''}
          </section>` : ''}
          <section class="rp-card">
            ${cardHead('추천·비추천 비율 비교', 'Steam 전체와 수집한 리뷰', 'scale')}
            <div class="rp-bal">
              ${bar('Steam 전체', pop.total ? `${num(pop.total)}건` : '', popNegRate)}
              ${bar('수집한 리뷰', `${num(collected)}건`, colNegRate)}
            </div>
            ${balanceNote ? `<p class="rp-callout">${balanceNote}</p>` : ''}
          </section>
          <section class="rp-card rp-caveat">
            ${cardHead('이 숫자를 읽을 때', '', 'info')}
            <ul>
              ${params.sort === 'random' && pool
                ? `<li><b>${since ? `${since} 이후 ` : ''}리뷰 ${num(pool.size)}건을 훑고 그중에서 무작위로 뽑았습니다.</b> ${pool.complete ? '확인한 리뷰 범위 안에서 무작위로 뽑았습니다.' : `수집 범위를 끝까지 확인하지 못해 최근 ${num(pool.size)}건 안에서만 뽑았습니다.`} 리뷰를 쓰지 않은 유저의 생각은 알 수 없습니다.</li>`
                : `<li><b>${sortLabel}으로 모은 리뷰입니다${since ? ` (${since} 이후)` : ''}.</b> 전체 유저를 무작위로 뽑은 표본이 아니므로 "전체 유저의 몇 %"로 읽으면 안 됩니다.</li>`}
              ${(() => { const vp = data?.evidence?.vote_periods, gap = data?.evidence?.vote_period_gap_days;
                if (!vp?.up || !vp?.down || gap == null) return '';
                const span = Math.max(vp.up.days, vp.down.days, 1);
                return gap > Math.max(7, span * .2)
                  ? `<li><b>추천과 비추천을 모은 기간이 ${num(gap)}일 어긋납니다.</b> 추천은 ${vp.up.start.replaceAll('-', '.')}부터, 비추천은 ${vp.down.start.replaceAll('-', '.')}부터입니다. 둘을 같은 시기의 의견으로 견주면 안 됩니다.</li>`
                  : ''; })()}
              ${design.n_total ? `<li><b>처음 계획은 ${num(design.n_total)}건</b>(추천 ${num(design.n_pos)} / 비추천 ${num(design.n_neg)})이었고, 실제 ${num(collected)}건 모았습니다.</li>` : ''}
              ${params.min_neg ? `<li>불만을 볼 수 있도록 비추천을 최소 <b>${num(params.min_neg)}건</b> 모으도록 설정했습니다.</li>` : ''}
            </ul>
          </section>
        </div>
        <div class="rp-duo is-even rp-full">${promiseHTML(data?.evidence?.promise)}${biasHTML(data?.evidence?.bias)}</div>
      </div>`;
  }

  // 표본 오차는 수집 범위·건수·목표 오차·분석 완료 조건을 모두 확인한 뒤 표시한다.
  function promiseHTML(p) {
    if (!p) return '';
    return `<section class="rp-card">
        ${cardHead('표본 오차의 조건', `계획 기준 ±${num(p.target)}% · 계획 ${num(p.planned)}건`, 'matrix')}
        <p class="rp-insight">${p.kept
          ? `<b>조건을 충족했습니다.</b> 확인한 리뷰 범위의 추천 비율을 표본에서 추정할 때 최대 오차는 95% 신뢰수준에서 ±${num(p.analyzed_margin)}%p입니다.`
          : `<b>통계적 오차로 해석할 수 없습니다.</b> ±${num(p.target)}%는 수집 건수를 정한 계획 기준입니다.`}</p>
        <ol class="rp-checks">${p.checks.map(c => `<li class="${c.ok ? 'is-ok' : 'is-no'}"><b>${c.ok ? '예' : '아니오'}</b><span>${esc(c.name)}<small>${esc(c.text)}</small></span></li>`).join('')}</ol>
        ${p.kept ? '' : '<p class="rp-callout">무작위로 뽑고, 범위를 끝까지 훑고, 계획한 만큼 모아야 오차범위를 말할 수 있습니다.</p>'}
        <p class="rp-note">이 오차는 수집 대상 리뷰의 추천 비율에만 해당합니다. 주제별 비율, AI 분류의 정확도, 리뷰를 쓰지 않은 유저는 포함하지 않습니다.</p>
      </section>`;
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
        ${cardHead('단계마다 치우침이 생기는가', '걸러질 때마다 남은 글의 성격이 달라지는지 봅니다', 'scale')}
        <table class="rd-cross"><thead><tr><td></td><th scope="col">비추천</th><th scope="col">플레이 시간</th><th scope="col">글 길이</th></tr></thead>
          <tbody>${rows.map(r => `<tr><th scope="row">${esc(r.name)}<small>${num(r.n)}건</small></th>
            ${cell(r, 'negative_rate', pct, () => 2)}${cell(r, 'hours', v => `${num(Math.round(v))}시간`, v => Math.max(5, v * .25))}${cell(r, 'length', v => `${num(v)}자`, v => Math.max(5, v * .5))}</tr>`).join('')}</tbody></table>
        <p class="rp-callout">주제 숫자가 나오는 <b>${esc(themed.name)} ${num(themed.n)}건</b>은 수집한 리뷰 전체보다 비추천이 <b>${Math.abs(gap).toFixed(1)}%p</b> ${hours == null ? (gap >= 0 ? '많습니다' : '적습니다') : `${gap >= 0 ? '많고' : '적고'}, 플레이 시간이 <b>${num(Math.abs(Math.round(hours)))}시간</b> ${hours >= 0 ? '깁니다' : '짧습니다'}`}.</p>
        <p class="rp-note">플레이 시간과 글 길이는 가운데 값입니다. 색이 칠해진 칸은 수집한 리뷰 전체와 차이가 큰 값입니다.</p>
      </section>`;
  }

  // 분석 설계서: 단계마다 누가 정했는지(사람 · AI · 규칙)와 무엇을 정했는지
  function designLogHTML(rows) {
    if (!rows?.length) return '';
    const whoClass = w => w.startsWith('사람') ? 'human' : w.startsWith('AI') ? 'ai' : 'rule';
    return `<section class="rp-card rp-design">
        ${cardHead('분석 순서와 담당', '단계마다 누가 처리했는지 적습니다. 사람은 무엇을·얼마나만 고르고, AI는 주제를 제안하고 분류하며, 나머지는 규칙이 처리합니다.', 'report')}
        <ol class="rp-steps">${rows.map(r => `<li>
          <span class="rp-step-name">${esc(r.step)}</span>
          <span class="rp-who ${whoClass(r.who)}">${esc(r.who)}</span>
          <span class="rp-step-text">${esc(r.text)}${r.details?.length ? `<ul>${r.details.map(d => `<li>${esc(d)}</li>`).join('')}</ul>` : ''}</span></li>`).join('')}</ol>
        <p class="rp-note">분석기는 숫자와 근거를 보여줍니다. 원문을 확인하고 무엇을 고칠지 정하는 판단은 사람의 몫입니다.</p>
      </section>`;
  }

  // AI 사용량: 단계별 호출 수와 토큰. 비용은 설정한 단가로 계산한 추정치다.
  function usageHTML(u) {
    if (!u) return '';
    const stages = [['A', '주제 찾기'], ['B', '전체 분류'], ['C', '불만 자세히 읽기'], ['D', '요약']].filter(([k]) => u[k]?.calls);
    if (!stages.length) return '';
    const tokens = k => (u[k].input || 0) + (u[k].output || 0);
    const total = stages.reduce((n, [k]) => n + tokens(k), 0) || 1;
    const price = u.pricing || {};
    const cost = stages.reduce((n, [k]) => n + (u[k].input || 0) * (price.input_per_1m || 0) + (u[k].output || 0) * (price.output_per_1m || 0), 0) / 1e6;
    return `<section class="rp-card rp-half">
        ${cardHead('AI 사용량', `합계 ${num(total)} 토큰${cost ? ` · 설정 단가 기준 약 $${cost.toFixed(cost < 1 ? 3 : 2)}` : ''}`, 'spark')}
        <div class="rp-split">${stages.map(([k, label], i) => `<i class="use-${i}" style="flex:${tokens(k)} 1 0" title="${label} ${num(tokens(k))} 토큰"></i>`).join('')}</div>
        <div class="rp-use-list">${stages.map(([k, label], i) => `<span><i class="use-${i}"></i>${label} <b>${num(u[k].calls)}회</b> · ${num(tokens(k))} 토큰</span>`).join('')}</div>
        <p class="rp-note">모델 ${esc(u.model || '')}.${u.stand_ins ? ` 이 모델이 붐빌 때 ${Object.entries(u.stand_ins).map(([name, count]) => `${esc(name)}이(가) ${num(count)}번`).join(', ')} 대신 답했습니다.` : ''} 본문이 같은 리뷰는 한 번만 보내고, 긴 리뷰는 앞·끝만 보내 토큰을 줄입니다.</p>
      </section>`;
  }

  // 품질 점검: 네 가지 점수를 같은 0~100 눈금 막대로. 80점 이상 통과, 60점 이상 주의.
  function qualityHTML(q) {
    if (!q || !q.dimensions) return '';
    const dims = [
      ['completeness', '완전성', '빠진 데이터가 없는가'],
      ['consistency', '일관성', 'AI 판단이 Steam 추천 여부와 크게 어긋나지 않는가'],
      ['accuracy', '분석 가능성', '너무 짧거나 판단이 어려운 리뷰가 적은가']];
    const pass = q.thresholds?.pass ?? 80, warn = q.thresholds?.warn ?? 60;
    const tone = v => v >= pass ? 'ok' : v >= warn ? 'warn' : 'bad';
    const issues = dims.flatMap(([k]) => q.dimensions[k]?.issues || []).slice(0, 4);
    return `<section class="rp-card rp-half">
        ${cardHead('자료 상태 점검', `세로선은 내부 기준 ${pass}점`, 'matrix')}
        <div class="rp-quality">${dims.map(([k, label, desc]) => {
          const v = q.dimensions[k]?.score;
          return v == null ? '' : `<div class="rp-q-row"><span class="rp-q-label"><b>${label}</b><small>${desc}</small></span><span class="rp-q-track"><i class="${tone(v)}" style="width:${Math.max(0, Math.min(100, v))}%"></i><em style="left:${pass}%"></em></span><span class="rp-q-val">${num(v)}</span></div>`;
        }).join('')}</div>
        <p class="rp-note">이 점수는 누락·추천 여부와의 일치·분석 가능성을 살피는 내부 기준이며 AI 정확도나 통계적 신뢰도를 뜻하지 않습니다.</p>
        ${issues.length ? `<ul class="rp-issues">${issues.map(i => `<li>${esc(i)}</li>`).join('')}</ul>` : ''}
      </section>`;
  }


  /* ---------------- 불만 분석 ---------------- */
  let deepTheme;
  function renderDeep(data) {
    const box = document.getElementById('rpDeep');
    if (!box) return;
    const deep = data?.evidence?.deep;
    if (!deep) { box.innerHTML = '<div class="rp-card rp-empty">분석 결과가 아직 없습니다.</div>'; return; }
    const counts = data.evidence.counts || {};
    deepTheme = deep.causes.find(c => c.terms.length)?.theme || deep.causes[0]?.theme;
    // 이 화면의 바탕 숫자는 머리말 한 줄로 적는다
    const topNeg = [...(data.evidence.themes || [])].sort((a, b) => b.neg - a.neg)[0];
    const worst = (data.evidence.cohorts || []).filter(c => !c.small && c.negative_rate != null).sort((a, b) => b.negative_rate - a.negative_rate)[0];
    box.innerHTML = `
      <div class="rd-kpis">
        ${counts.complaint_reviews != null ? kpi('불만이 담긴 리뷰', 'down', num(counts.complaint_reviews), '건', share(counts.complaint_reviews, counts.analyzed), `AI 분석 ${num(counts.analyzed)}건 중`, 'div', '', 'is-neg') : ''}
        ${counts.recommended_complaints != null ? kpi('불만을 쓰고도 추천', 'up', num(counts.recommended_complaints), '건', share(counts.recommended_complaints, counts.complaint_reviews), '불만이 담긴 리뷰 중', 'div', '', '') : ''}
        ${topNeg?.neg ? kpi('불만이 가장 많은 주제', 'scale', esc(topNeg.name), '', '', `불만 ${num(topNeg.neg)}건 · 칭찬 ${num(topNeg.pos)}건`, 'div', '', 'is-neg') : ''}
        ${worst ? kpi('비추천율이 가장 높은 구간', 'clock', esc(worst.label), '', pct(worst.negative_rate), `${num(worst.n)}건 중 비추천 ${num(worst.negative)}건`, 'div', '', 'is-neg') : ''}
      </div>
      <div class="rp-grid">
        <section class="rp-card rp-third" id="rpCauses"></section>
        <section class="rp-card rp-third" id="rpWants"></section>
        <section class="rp-card rp-third" id="rpAgreed"></section>
        <section class="rp-card rp-half">${cardHead('플레이 시간별 비추천율', `점선은 전체 ${pct(data.evidence.sample_negative_rate)}`, 'clock')}<div id="rdCohortChart"></div><p class="rp-note">리뷰를 쓸 당시의 플레이 시간으로 나눴습니다.</p></section>
        <section class="rp-card rp-half" id="rpChurn"></section>
      </div>`;
    window.ReviewDashboard?.cohorts?.();
    box.onclick = e => { const b = e.target.closest('[data-cause]'); if (b) { deepTheme = b.dataset.cause; drawCauses(deep); } };
    drawCauses(deep); drawChurn(deep); drawAgreed(deep); drawWants(deep);
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
    cause: (deep, theme) => {
      const c = deep.causes.find(x => x.theme === theme), top = c?.terms[0];
      return top ? `<b>${esc(c.theme)}</b> 불만에서 가장 많이 나온 말은 <b>'${esc(top.word)}'</b>입니다 (${num(top.count)}건).` : '';
    },
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
  const noComplaints = '<p class="rp-empty">불만 리뷰의 AI 메모(complaints_v3.jsonl)가 없습니다. 분석을 다시 실행하면 채워집니다.</p>';
  const head = (iconName, title, sub, insight) => cardHead(title, sub, iconName) + (insight ? `<p class="rp-insight">${insight}</p>` : '');
  const hbar = (label, sub, value, max, text, cls = '') => `<div class="rp-hrow ${cls}"><span class="rp-hlabel"><b>${label}</b>${sub ? `<small>${sub}</small>` : ''}</span><span class="rp-htrack"><i style="width:${max ? value / max * 100 : 0}%"></i></span><span class="rp-hval">${text}</span></div>`;

  // 불만에서 자주 나온 말
  function drawCauses(deep) {
    const el = document.getElementById('rpCauses');
    const c = deep.causes.find(x => x.theme === deepTheme);
    const title = '불만에서 자주 나온 말', sub = '주제를 고르면 그 불만에 자주 나온 낱말을 봅니다';
    const tabs = `<div class="rp-chips">${deep.causes.map(x => `<button class="rp-chip" data-cause="${esc(x.theme)}" aria-pressed="${x.theme === deepTheme}">${esc(x.theme)}</button>`).join('')}</div>`;
    if (!deep.has_complaints || !c) { el.innerHTML = head('down', title, sub) + noComplaints; return; }
    const top = c.terms[0], max = top?.count || 1;
    const seen = new Set(), once = text => seen.has(text) ? '' : (seen.add(text), `“${esc(text)}”`);
    el.innerHTML = head('down', title, sub, lines.cause(deep, deepTheme))
      + tabs
      + (c.terms.length ? `<div class="rp-hbars">${c.terms.map((t, i) => hbar(esc(t.word), once(t.example), t.count, max, `${num(t.count)}건`, i ? '' : 'is-top')).join('')}</div>`
        : '<p class="rp-empty">여러 리뷰가 함께 쓴 말이 없습니다.</p>')
      + `<p class="rp-note">${esc(c.theme)} 불만 리뷰 ${num(c.reviews)}건에 AI가 적어 둔 문제·원인 메모에서, 두 리뷰 이상이 쓴 낱말입니다.</p>`;
  }

  // 플레이 시간이 짧은 비추천 리뷰의 불만. 이탈했는지, 그 때문인지는 알 수 없으므로 '이탈 원인'이라고 부르지 않는다.
  function drawChurn(deep) {
    const el = document.getElementById('rpChurn');
    const {early_n, later_n, topics} = deep.churn, h = deep.early_hours;
    const title = `${h}시간 미만 비추천에서 많이 나온 주제`, sub = `${h}시간 전후로 나눈 비추천 리뷰 비교`;
    if (!early_n) { el.innerHTML = head('clock', title, sub) + `<p class="rp-empty">${h}시간 전에 비추천한 리뷰가 없습니다.</p>`; return; }
    const gap = churnGap, pick = churnPick(deep);
    const max = Math.max(...topics.flatMap(t => [t.early_share || 0, t.later_share || 0]), 1);
    el.innerHTML = head('clock', title, sub, lines.churn(deep))
      + `<div class="rp-legend"><span class="early">${h}시간 전 비추천 ${num(early_n)}건</span><span class="later">${h}시간 뒤 비추천 ${num(later_n)}건</span></div>`
      + `<div class="rp-pairs">${topics.map(t => `<div class="rp-pair ${t === pick && gap(pick) > 5 && churnEnough(pick, deep.churn) ? 'is-top' : ''}"><b>${esc(t.name)}</b>
          <span class="rp-ptrack early"><i style="width:${(t.early_share || 0) / max * 100}%"></i><em>${pct(t.early_share ?? 0)} · ${num(t.early)}/${num(early_n)}건</em></span>
          <span class="rp-ptrack later"><i style="width:${(t.later_share || 0) / max * 100}%"></i><em>${pct(t.later_share ?? 0)} · ${num(t.later)}/${num(later_n)}건</em></span></div>`).join('')}</div>`
      + `<p class="rp-note">비율 = 각 묶음의 비추천 리뷰 중 그 주제를 불만으로 말한 비율. 건수가 한 자릿수인 줄은 리뷰 한두 개로 뒤집히니 참고만 하세요. 플레이 시간이 짧다고 게임을 그만뒀다는 뜻은 아니고, 이 불만 때문에 비추천했는지도 알 수 없습니다.</p>`;
  }

  // Steam 도움됨이 많은 불만. 도움됨은 오래되거나 많이 노출된 리뷰일수록 많으므로 '공감'이라고 단정하지 않는다.
  function drawAgreed(deep) {
    const el = document.getElementById('rpAgreed');
    const {topics, total_votes, reviews} = deep.agreed;
    const title = 'Steam 도움됨이 많은 불만', sub = '불만 건수 비율과 도움됨 비율을 나란히 봅니다';
    if (!total_votes) { el.innerHTML = head('up', title, sub) + '<p class="rp-empty">불만 리뷰에 "도움됨"이 눌린 기록이 없습니다.</p>'; return; }
    const gap = agreeGap, pick = agreePick(deep);
    const max = Math.max(...topics.flatMap(t => [t.vote_share || 0, t.count_share || 0]), 1);
    const quote = reviews[0];
    el.innerHTML = head('up', title, sub, lines.agreed(deep))
      + `<div class="rp-legend"><span class="later">불만 건수 비율</span><span class="agree">도움됨 비율</span></div>`
      + `<div class="rp-pairs">${topics.map(t => `<div class="rp-pair ${t === pick && gap(pick) > 5 && agreeEnough(pick, deep.agreed) ? 'is-top' : ''}"><b>${esc(t.name)}</b>
          <span class="rp-ptrack later"><i style="width:${(t.count_share || 0) / max * 100}%"></i><em>${pct(t.count_share)}</em></span>
          <span class="rp-ptrack agree"><i style="width:${(t.vote_share || 0) / max * 100}%"></i><em>${pct(t.vote_share)} · ${num(t.votes)}</em></span></div>`).join('')}</div>`
      + (quote ? `<figure class="rp-quote"><figcaption>도움됨이 가장 많은 불만 리뷰 · 도움됨 ${num(quote.helpful)} · ${quote.themes.map(esc).join(', ')}</figcaption><blockquote>${esc(window.ReviewDashboard?.plain?.(quote.content) ?? quote.content)}${quote.truncated ? '…' : ''}</blockquote></figure>` : '')
      + '<p class="rp-note">도움됨은 오래된 리뷰나 많이 노출된 리뷰일수록 많이 받습니다. 유저 전체가 공감한 정도로 읽지 마세요.</p>';
  }

  // 유저가 원하는 것
  function drawWants(deep) {
    const el = document.getElementById('rpWants');
    const title = '유저가 원하는 것', sub = '불만 리뷰에 적힌 구체적인 요청';
    if (!deep.has_complaints) { el.innerHTML = head('bulb', title, sub) + noComplaints; return; }
    const list = deep.wants.slice(0, 5);
    el.innerHTML = head('bulb', title, sub, lines.wants(deep))
      + (list.length ? `<ol class="rp-wants">${list.map(w => `<li><span class="rp-want-text">${esc(w.text)}</span><span class="rp-want-meta"><em>${esc(w.theme)}</em>${num(w.count)}건 · 도움됨 ${num(w.votes)}</span></li>`).join('')}</ol>`
        : '<p class="rp-empty">구체적인 요청 문장이 없습니다.</p>')
      + '<p class="rp-note">"~해 주세요", "~했으면" 같은 구체적인 요청만 모았습니다. "버그 수정"처럼 막연한 말은 뺐습니다.</p>';
  }

  // 한 장 보고서용: 심층 분석 네 줄 (비어 있는 줄은 뺌)
  function deepLines(deep) {
    if (!deep) return [];
    const theme = deep.causes.find(c => c.terms.length)?.theme;
    return [['불만에서 자주 나온 말', deep.has_complaints ? lines.cause(deep, theme) : ''], [`${deep.early_hours}시간 미만 비추천`, lines.churn(deep)],
            ['Steam 도움됨', lines.agreed(deep)], ['유저 요청', deep.has_complaints ? lines.wants(deep) : '']].filter(([, t]) => t);
  }

  return {renderReviews, renderSample, renderDeep, deepLines};
})();
