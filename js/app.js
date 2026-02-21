/**
 * 부동산 청약 대시보드 — 프론트엔드 로직
 */
(function () {
  'use strict';

  let allItems = [];
  let activeFilters = { status: 'upcoming', region: 'all' };
  let activeSort = 'date-asc';

  // ===== 초기화 =====
  document.addEventListener('DOMContentLoaded', init);

  async function init() {
    await loadData();
    bindEvents();
    handleHashChange();
    window.addEventListener('hashchange', handleHashChange);
  }

  async function loadData() {
    try {
      const resp = await fetch('data/subscriptions.json');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      allItems = data.items || [];
      document.getElementById('updatedAt').textContent =
        `최종 업데이트: ${data.updated_at || '-'}`;

      updateSummary(data);
      renderCards();
    } catch (e) {
      console.error('데이터 로드 실패:', e);
      document.getElementById('cardList').innerHTML = `
        <div class="empty-state">
          <div class="icon">⚠</div>
          <h3>데이터를 불러올 수 없습니다</h3>
          <p>data/subscriptions.json 파일이 아직 생성되지 않았거나 접근할 수 없습니다.</p>
          <p style="margin-top:12px;font-size:13px;color:var(--text-muted)">
            python scripts/main.py 를 실행하여 데이터를 생성하세요.
          </p>
        </div>`;
    }
  }

  // ===== 요약 통계 =====
  function updateSummary(data) {
    const items = data.items || [];
    document.getElementById('totalCount').textContent = items.length;
    document.getElementById('upcomingCount').textContent =
      items.filter(i => i.status === '접수예정').length;
    document.getElementById('openCount').textContent =
      items.filter(i => i.status === '접수중').length;

    const maxP = items.length > 0
      ? Math.max(...items.map(i => i.max_profit || 0))
      : 0;
    document.getElementById('maxProfit').textContent =
      maxP > 0 ? formatMoney(maxP) : '-';
  }

  // ===== 이벤트 바인딩 =====
  function bindEvents() {
    // 필터 버튼
    document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const filterType = btn.dataset.filter;
        const value = btn.dataset.value;

        // 같은 그룹 내 active 토글
        btn.closest('.filter-group')
          .querySelectorAll('.filter-btn')
          .forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        activeFilters[filterType] = value;
        renderCards();
      });
    });

    // 정렬
    document.getElementById('sortSelect').addEventListener('change', (e) => {
      activeSort = e.target.value;
      renderCards();
    });

    // 상세 닫기 (overlay 클릭)
    document.getElementById('detailOverlay').addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeDetail();
    });

    // ESC로 닫기
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeDetail();
    });
  }

  // ===== 카드 렌더링 =====
  function renderCards() {
    const container = document.getElementById('cardList');
    let filtered = applyFilters(allItems);
    filtered = applySort(filtered);

    if (filtered.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="icon">🏠</div>
          <h3>조건에 맞는 청약이 없습니다</h3>
          <p>필터를 변경하거나 데이터 업데이트를 기다려주세요.</p>
        </div>`;
      return;
    }

    container.innerHTML = filtered.map(item => createCard(item)).join('');

    // 카드 클릭 이벤트
    container.querySelectorAll('.card').forEach(card => {
      card.addEventListener('click', () => {
        const id = card.dataset.id;
        window.location.hash = `#detail/${id}`;
      });
    });
  }

  function createCard(item) {
    const statusClass = getStatusClass(item.status);
    const bestModel = getBestModel(item.models);

    const subType = item.subscription_type || 'APT';
    const typeBadge = subType !== 'APT'
      ? `<span class="card-type">${esc(subType)}</span>`
      : '';

    return `
      <div class="card" data-id="${item.id}">
        <span class="card-status ${statusClass}">${item.status}</span>
        ${typeBadge}
        <div class="card-name">${esc(item.name)}</div>
        <div class="card-location">${esc(item.sido || item.region)} ${esc(item.sigungu || '')} ${esc(item.address || '')}</div>
        <div class="card-constructor">${esc(item.constructor || '')} · 총 ${item.total_households || '-'}세대</div>
        <div class="card-metrics">
          ${bestModel ? `
            <div class="metric">
              <div class="label">분양가 (3.3㎡)</div>
              <div class="value">${formatMoney(bestModel.price_per_pyeong)}</div>
            </div>
            <div class="metric">
              <div class="label">예상시세</div>
              <div class="value">${formatMoney(bestModel.market_price)}</div>
            </div>
          ` : ''}
          <div class="profit-metric" ${item.max_profit <= 0 ? 'style="background:var(--bg-secondary);border-color:var(--border)"' : ''}>
            <div class="label" ${item.max_profit <= 0 ? 'style="color:var(--text-muted)"' : ''}>예상 최대 차익</div>
            <div class="value" ${item.max_profit <= 0 ? 'style="color:var(--text-secondary);text-shadow:none"' : ''}>${item.max_profit > 0 ? formatMoney(item.max_profit) : '시세 분석중'}</div>
            ${bestModel?.price_source ? `<div class="source">${esc(bestModel.price_source)}</div>` : ''}
          </div>
        </div>
        <div class="card-dates">
          <span>${item.schedule?.receipt_start ? '접수: ' + item.schedule.receipt_start : (item.schedule?.announcement_date ? '공고: ' + item.schedule.announcement_date : '접수: -')}</span>
          <span>당첨발표: ${item.schedule?.winner_announce_date || '-'}</span>
        </div>
      </div>`;
  }

  // ===== 상세 뷰 =====
  function openDetail(id) {
    const item = allItems.find(i => i.id === id);
    if (!item) return;

    const overlay = document.getElementById('detailOverlay');
    const panel = document.getElementById('detailPanel');

    panel.innerHTML = buildDetailHTML(item);
    overlay.classList.add('active');
    document.body.style.overflow = 'hidden';

    // 닫기 버튼
    panel.querySelector('.detail-close').addEventListener('click', closeDetail);
  }

  function closeDetail() {
    document.getElementById('detailOverlay').classList.remove('active');
    document.body.style.overflow = '';
    if (window.location.hash.startsWith('#detail/')) {
      history.pushState(null, '', window.location.pathname);
    }
  }

  function buildDetailHTML(item) {
    const reg = item.regulations || {};
    const schedule = item.schedule || {};
    const bestModel = getBestModel(item.models);

    return `
      <button class="detail-close" title="닫기">&times;</button>

      <div class="detail-header">
        <span class="card-status ${getStatusClass(item.status)}">${item.status}</span>
        <h2>${esc(item.name)}</h2>
        <div class="location">${esc(item.sido || item.region)} ${esc(item.sigungu || '')} ${esc(item.address || '')}</div>
        <div class="constructor">${esc(item.constructor || '')} · 총 ${item.total_households || '-'}세대</div>
      </div>

      <div class="detail-blocks">
        <!-- 1. 청약 일정 -->
        <div class="detail-block">
          <h3>청약 일정</h3>
          <div class="info-grid">
            <div class="info-item">
              <span class="label">모집공고일</span>
              <span class="value">${schedule.announcement_date || '-'}</span>
            </div>
            <div class="info-item">
              <span class="label">특별공급일</span>
              <span class="value">${schedule.special_supply_date || '-'}</span>
            </div>
            <div class="info-item">
              <span class="label">1순위 접수일</span>
              <span class="value">${schedule.first_priority_date || '-'}</span>
            </div>
            <div class="info-item">
              <span class="label">2순위 접수일</span>
              <span class="value">${schedule.second_priority_date || '-'}</span>
            </div>
            <div class="info-item">
              <span class="label">당첨자 발표일</span>
              <span class="value highlight">${schedule.winner_announce_date || '-'}</span>
            </div>
            <div class="info-item">
              <span class="label">계약기간</span>
              <span class="value">${schedule.contract_start || '-'} ~ ${schedule.contract_end || '-'}</span>
            </div>
            <div class="info-item">
              <span class="label">입주예정</span>
              <span class="value">${schedule.move_in_date || '-'}</span>
            </div>
          </div>
        </div>

        <!-- 2. 자격 요건 -->
        <div class="detail-block">
          <h3>자격 요건</h3>
          <div class="info-grid">
            <div class="info-item">
              <span class="label">접수유형</span>
              <span class="value">${esc(item.qualification?.region_limit || '-')}</span>
            </div>
            <div class="info-item">
              <span class="label">주택구분</span>
              <span class="value">${esc(item.qualification?.house_type || '-')}</span>
            </div>
            <div class="info-item full-width">
              <span class="label">일반 자격요건</span>
              <span class="value">만 19세 이상 세대주, 청약통장 가입 12개월+, 무주택세대 구성원</span>
            </div>
          </div>
        </div>

        <!-- 3. 전매제한 -->
        <div class="detail-block regulation">
          <h3>전매제한</h3>
          <div class="info-grid">
            ${buildRegulationBadges(reg)}
            <div class="info-item">
              <span class="label">전매제한 기간</span>
              <span class="value warn">${reg.resale_restriction?.period || '-'}</span>
            </div>
            <div class="info-item full-width">
              <span class="label">상세</span>
              <span class="value">${reg.resale_restriction?.detail || '-'}</span>
            </div>
          </div>
        </div>

        <!-- 4. 재당첨 제한 -->
        <div class="detail-block regulation">
          <h3>재당첨 제한</h3>
          <div class="info-grid">
            <div class="info-item">
              <span class="label">제한 기간</span>
              <span class="value warn">${reg.rewin_restriction?.period || '-'}</span>
            </div>
            <div class="info-item full-width">
              <span class="label">상세</span>
              <span class="value">${reg.rewin_restriction?.detail || '-'}</span>
            </div>
          </div>
        </div>

        <!-- 5. 거주의무 -->
        <div class="detail-block regulation">
          <h3>거주의무</h3>
          <div class="info-grid">
            <div class="info-item">
              <span class="label">의무 기간</span>
              <span class="value ${reg.residency_obligation?.required ? 'warn' : ''}">${reg.residency_obligation?.period || '-'}</span>
            </div>
            <div class="info-item full-width">
              <span class="label">상세</span>
              <span class="value">${reg.residency_obligation?.detail || '-'}</span>
            </div>
          </div>
        </div>

        <!-- 6. 투자 분석 (주택형별) -->
        <div class="detail-block investment">
          <h3>투자 분석</h3>
          ${buildModelsTable(item.models)}
          ${bestModel?.funding ? buildFundingDetail(bestModel) : ''}
        </div>
      </div>
    `;
  }

  function buildRegulationBadges(reg) {
    let badges = '<div class="info-item full-width" style="margin-bottom:8px">';
    if (reg.is_speculative_zone) badges += '<span class="regulation-badge severe">투기과열지구</span>';
    if (reg.is_adjusted_zone) badges += '<span class="regulation-badge moderate">조정대상지역</span>';
    if (reg.is_price_cap) badges += '<span class="regulation-badge moderate">분양가상한제</span>';
    if (reg.is_public_zone) badges += '<span class="regulation-badge mild">공공주택지구</span>';
    if (!reg.is_speculative_zone && !reg.is_adjusted_zone && !reg.is_price_cap && !reg.is_public_zone) {
      badges += '<span class="regulation-badge mild">비규제지역</span>';
    }
    badges += '</div>';
    return badges;
  }

  function buildModelsTable(models) {
    if (!models || models.length === 0) return '<p style="color:var(--text-muted)">주택형 정보 없음</p>';

    let html = `
      <table class="model-table">
        <thead>
          <tr>
            <th>주택형</th>
            <th>전용면적</th>
            <th>분양가</th>
            <th>예상시세</th>
            <th>예상차익</th>
            <th>세대수</th>
          </tr>
        </thead>
        <tbody>`;

    for (const m of models) {
      html += `
        <tr>
          <td>${esc(m.housing_type || '-')}</td>
          <td>${m.exclusive_area ? m.exclusive_area.toFixed(1) + '㎡' : '-'}</td>
          <td>${formatMoney(m.supply_price)}</td>
          <td>${formatMoney(m.market_price)}</td>
          <td class="profit-cell">${formatMoney(m.profit)}</td>
          <td>${m.household_count || '-'}</td>
        </tr>`;
    }

    html += '</tbody></table>';
    return html;
  }

  function buildFundingDetail(model) {
    const f = model.funding;
    if (!f) return '';

    return `
      <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
        <div style="font-size:13px;font-weight:600;color:var(--accent-green);margin-bottom:12px">
          예상 필요자금 상세 (최대 차익 주택형 기준)
        </div>
        <div class="info-grid">
          <div class="info-item">
            <span class="label">분양가 총액</span>
            <span class="value">${formatMoney(model.supply_price)}</span>
          </div>
          <div class="info-item">
            <span class="label">예상시세</span>
            <span class="value">${formatMoney(model.market_price)}</span>
          </div>
          <div class="info-item">
            <span class="label">계약금 (10%)</span>
            <span class="value">${formatMoney(f.down_payment)}</span>
          </div>
          <div class="info-item">
            <span class="label">중도금 (60%)</span>
            <span class="value">${formatMoney(f.interim_payment)}</span>
          </div>
          <div class="info-item">
            <span class="label">잔금 (30%)</span>
            <span class="value">${formatMoney(f.balance)}</span>
          </div>
          <div class="info-item">
            <span class="label">예상 전세가 (전세가율 ${(f.jeonse_ratio * 100).toFixed(0)}%)</span>
            <span class="value">${formatMoney(f.estimated_jeonse)}</span>
          </div>
          <div class="info-item">
            <span class="label">중도금 이자 (연 4%, 2년)</span>
            <span class="value">${formatMoney(f.interim_interest)}</span>
          </div>
          <div class="info-item">
            <span class="label">예상 실투자금</span>
            <span class="value highlight" style="font-size:18px">${formatMoney(f.actual_investment)}</span>
          </div>
        </div>
      </div>`;
  }

  // ===== 필터/정렬 =====
  function applyFilters(items) {
    return items.filter(item => {
      if (activeFilters.status === 'upcoming') {
        if (item.status !== '접수예정' && item.status !== '접수중') return false;
      } else if (activeFilters.status !== 'all' && item.status !== activeFilters.status) {
        return false;
      }
      if (activeFilters.region !== 'all' && item.region !== activeFilters.region) return false;
      return true;
    });
  }

  function applySort(items) {
    const sorted = [...items];
    switch (activeSort) {
      case 'profit-desc':
        sorted.sort((a, b) => (b.max_profit || 0) - (a.max_profit || 0));
        break;
      case 'profit-asc':
        sorted.sort((a, b) => (a.max_profit || 0) - (b.max_profit || 0));
        break;
      case 'date-asc':
        sorted.sort((a, b) => bestDate(a) - bestDate(b));
        break;
      case 'date-desc':
        sorted.sort((a, b) => bestDate(b) - bestDate(a));
        break;
    }
    return sorted;
  }

  // ===== Hash routing =====
  function handleHashChange() {
    const hash = window.location.hash;
    if (hash.startsWith('#detail/')) {
      const id = hash.replace('#detail/', '');
      openDetail(id);
    } else {
      closeDetail();
    }
  }

  // ===== 유틸리티 =====
  function formatMoney(amount) {
    if (!amount && amount !== 0) return '-';
    const absAmount = Math.abs(amount);
    const sign = amount < 0 ? '-' : '';

    if (absAmount >= 100000000) {
      const eok = Math.floor(absAmount / 100000000);
      const man = Math.floor((absAmount % 100000000) / 10000);
      return sign + eok + '억' + (man > 0 ? ' ' + man.toLocaleString() + '만' : '');
    }
    if (absAmount >= 10000) {
      return sign + Math.floor(absAmount / 10000).toLocaleString() + '만';
    }
    return sign + absAmount.toLocaleString() + '원';
  }

  function dateVal(dateStr) {
    if (!dateStr) return 0;
    return new Date(dateStr).getTime() || 0;
  }

  function bestDate(item) {
    const s = item.schedule || {};
    return dateVal(s.receipt_start) || dateVal(s.announcement_date) || 0;
  }

  function getStatusClass(status) {
    switch (status) {
      case '접수예정': return 'upcoming';
      case '접수중': return 'open';
      case '접수마감': return 'closed';
      default: return 'ended';
    }
  }

  function getBestModel(models) {
    if (!models || models.length === 0) return null;
    return models.reduce((best, m) =>
      (m.profit || 0) > (best.profit || 0) ? m : best
    , models[0]);
  }

  function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
})();
