// 画面名の定数化（タイポによる事故を構造的に防止）
const SCREENS = {
    START: 'start',
    TEST: 'test',
    RESULT: 'result'
};

// スコア判定のしきい値テーブル（if-elseチェーンをlookup化）
const SCORE_TIERS = [
    {
        min: 85,
        status: "🟢 【防衛運転のスペシャリスト】リスク予知型",
        commentary: `
            <strong>【分析】:</strong> あなたの運転は極めて論理的で、事故発生リスクを最小化する設計になっています。<br><br>
            <strong>【現場アドバイス】:</strong> 現状のスタイルを維持しつつ、今後は「イレギュラー発生時の柔軟な報連相」にリソースを割いてください。完璧を求めすぎて自身を追い詰めないよう、トラブル時は一人で抱え込まず、運行管理と連携する判断基準をさらに研ぎ澄ませましょう。<br><br>
            <strong>【推奨スキル】:</strong> 異常時プロトコルの更なる深掘り。
        `
    },
    {
        min: 65,
        status: "🔵 【物流の円滑剤】協調・マナー重視型",
        commentary: `
            <strong>【分析】:</strong> 現場のコミュニケーションにおいて、不要な摩擦を避ける能力が高いです。<br><br>
            <strong>【現場アドバイス】:</strong> 協調性は武器ですが、それが「自己の運行リズムの犠牲」になっていないか再評価が必要です。強引な他車に対して道を譲りすぎることは、後続車への連鎖的なブレーキを招きます。「譲るべきポイント」と「毅然と走るべきポイント」を、交通流のアルゴリズムに従って切り分けましょう。<br><br>
            <strong>【推奨スキル】:</strong> 交通流最適化（全体最適の視点）。
        `
    },
    {
        min: 45,
        status: "⚪ 【冷静なルーティン】鉄のメンタル型",
        commentary: `
            <strong>【分析】:</strong> 感情を排除した機械的な操作が可能で、長時間のパフォーマンス維持に長けています。<br><br>
            <strong>【現場アドバイス】:</strong> 淡々とした対応は現場で「無愛想」と誤認されるリスクがあります。荷役先での「事実ベースの簡潔な挨拶」や「確認作業の目視＋呼称」といった、外部に伝わる動作をルーティンに組み込んでください。内面の冷静さと、外界へのシグナリングを同期させることが重要です。<br><br>
            <strong>【推奨スキル】:</strong> 非言語コミュニケーションの最適化。
        `
    },
    {
        min: 25,
        status: "🟡 【効率重視の戦略家】タイムマネジメント型",
        commentary: `
            <strong>【分析】:</strong> 配送効率を最大化するルート設計・運行能力が高いです。<br><br>
            <strong>【現場アドバイス】:</strong> 効率化を追求するあまり、「安全のマージン」が削られています。黄色信号での進入や車間詰めは、事故という「コスト」が発生した瞬間に全てが破綻します。数分の遅れは「運行の不確定要素」として許容し、安全走行を最優先する定数として組み込んでください。<br><br>
            <strong>【推奨スキル】:</strong> リスク管理を前提とした運行計画の見直し。
        `
    },
    {
        min: -Infinity,
        status: "🔴 【改善の余地あり】感情先行・直感運転型",
        commentary: `
            <strong>【分析】:</strong> 現場での対応速度は速いですが、事故発生の因果関係を無視した「直感操作」が目立ちます。<br><br>
            <strong>【現場アドバイス】:</strong> 運転に「感情（怒りや焦り）」を混ぜることは、アルゴリズムのバグを放置するのと同じです。次の操作を行う前に「0.5秒の間」を置いてください。その間に現在の交通状況と荷崩れのリスクを再計算するだけで、挙動は劇的に安定します。まずは指差呼称の徹底から始めてください。<br><br>
            <strong>【推奨スキル】:</strong> 感情と操作のデカップリング（切り離し）。
        `
    }
];

let userAnswers = [];
let shuffledQuestions = [];
const testContainer = document.getElementById('test-container');

function shuffleArray(array) {
    const arr = [...array];
    for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
}

function createRipple({ currentTarget, clientX, clientY }) {
    const button = currentTarget;
    const circle = document.createElement('span');
    const diameter = Math.max(button.clientWidth, button.clientHeight);
    const radius = diameter / 2;
    const rect = button.getBoundingClientRect();

    circle.style.width = circle.style.height = `${diameter}px`;
    circle.style.left = `${clientX - rect.left - radius}px`;
    circle.style.top = `${clientY - rect.top - radius}px`;
    circle.classList.add('ripple');

    const existing = button.getElementsByClassName('ripple')[0];
    if (existing) existing.remove();

    button.appendChild(circle);
}

function initTest() {
    testContainer.innerHTML = '';
    shuffledQuestions.forEach((qData, index) => {
        const card = document.createElement('div');
        card.className = `test-card ${index === 0 ? 'active' : ''}`;
        card.id = `question-${index}`;

        const progress = (index / shuffledQuestions.length) * 100;

        // shuffledOptionsはstartTest内で既に付与済み（testDataを汚染しないための設計）
        card.innerHTML = `
            <div class="progress-container"><div class="progress-bar" style="width: ${progress}%"></div></div>
            <div style="font-size:0.8rem; color:#636e72;">質問 ${index + 1} / ${shuffledQuestions.length}</div>
            <div class="question-text">${qData.question}</div>
            <ul style="list-style:none; padding:0;">
                ${qData.shuffledOptions.map((opt, oIdx) => `
                    <li><button class="option-btn" data-q-index="${index}" data-o-index="${oIdx}">${opt.text}</button></li>
                `).join('')}
                <li><button class="option-btn skip-btn" data-q-index="${index}" data-o-index="-1">状況が判断できない／該当なし</button></li>
            </ul>
        `;
        testContainer.appendChild(card);
    });
}

// テストカード内のクリックをイベント委譲でまとめて処理
// （inline onclickへの文字列埋め込みをやめたことで、テキスト内のクオート事故が起きなくなる）
testContainer.addEventListener('click', (event) => {
    const button = event.target.closest('.option-btn');
    if (!button) return;

    createRipple({ currentTarget: button, clientX: event.clientX, clientY: event.clientY });

    const qIdx = Number(button.dataset.qIndex);
    const oIdx = Number(button.dataset.oIndex);

    // リップルが見えるように少し待ってから画面を切り替える
    setTimeout(() => {
        if (oIdx === -1) {
            handleAnswer(qIdx, -1, '回答なし', 0);
            return;
        }

        const opt = shuffledQuestions[qIdx].shuffledOptions[oIdx];
        handleAnswer(qIdx, oIdx, opt.text, opt.score);
    }, 300); // リップルのanimation(0.6s)より短めに、でも見える程度
});

// 画面切り替え（start / test / result）をまとめて管理する関数
function showScreen(screen) {
    document.getElementById('start-screen').style.display = screen === SCREENS.START ? 'block' : 'none';
    document.getElementById('test-container').style.display = screen === SCREENS.TEST ? 'block' : 'none';
    document.getElementById('back-to-top-link').style.display = screen === SCREENS.TEST ? 'block' : 'none';
    document.getElementById('result-area').classList.toggle('show', screen === SCREENS.RESULT);
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// 過去の受診履歴をスタート画面のリストに描画する
function renderPastScores() {
    const listEl = document.getElementById('past-scores-list');
    const scores = getPastScores();
    if (scores.length === 0) {
        listEl.innerHTML = '<li class="empty-msg">まだ受診履歴がありません。1回目の診断を受けてみてください！</li>';
        return;
    }
    listEl.innerHTML = scores.map(s => `
        <li><span>${s.takenAt}</span><span>${s.score}点</span></li>
    `).join('');
}

window.startTest = function() {
    userAnswers = [];
    // testDataの質問オブジェクトを直接書き換えないよう、新しいオブジェクトとしてコピーしてから
    // shuffledOptionsを持たせる（マスターデータの不変性を守るための修正）
    shuffledQuestions = shuffleArray(testData).map(qData => ({
        ...qData,
        shuffledOptions: shuffleArray(qData.options)
    }));
    initTest();
    showScreen(SCREENS.TEST);
};

window.handleAnswer = function(qIdx, oIdx, text, score) {
    userAnswers[qIdx] = { question: shuffledQuestions[qIdx].question, answer: text, score: score };
    document.getElementById(`question-${qIdx}`).classList.remove('active');

    if (qIdx + 1 < shuffledQuestions.length) {
        // まだ次の問題がある場合
        document.getElementById(`question-${qIdx + 1}`).classList.add('active');
    } else {
        // 全問終了時の処理
        const totalScore = userAnswers.reduce((sum, a) => sum + (a ? a.score : 0), 0);
        document.getElementById('total-score').textContent = totalScore;
        saveScore(totalScore);

        // スコアに応じた判定をテーブルから検索（if-elseチェーンの解消）
        const tier = SCORE_TIERS.find(t => totalScore >= t.min);
        document.getElementById('result-status').innerHTML = tier.status;
        document.getElementById('result-commentary').innerHTML = tier.commentary;

        // ▼回答内訳の流し込み▼
        const breakdownHtml = userAnswers.map((ans, idx) => {
            if (!ans) return '';
            return `
                <div class="breakdown-item">
                    <div class="breakdown-q">Q${idx + 1}: ${ans.question}</div>
                    <div class="breakdown-a">回答: ${ans.answer} <span class="breakdown-s">(${ans.score}点)</span></div>
                </div>
            `;
        }).join('');
        document.getElementById('breakdown-container').innerHTML = breakdownHtml;

        showScreen(SCREENS.RESULT);
    }
};

window.resetTest = function() {
    userAnswers = [];
    renderPastScores();
    showScreen(SCREENS.START);
};

async function boot() {
    await initDb();
    renderPastScores();
    showScreen(SCREENS.START);
}

boot();
