let db = null;
const STORAGE_KEY = 'truck_driver_test_db_v1';

// テーブル定義を関数化（初回作成時とリカバリ時の二重管理を防ぐ）
function createSchema() {
    db.run(`
        CREATE TABLE scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score INTEGER,
            taken_at TEXT
        );
    `);
}

async function initDb() {
    const SQL = await initSqlJs({
        locateFile: file => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/${file}`
    });

    const savedDb = localStorage.getItem(STORAGE_KEY);

    try {
        if (savedDb) {
            // Base64文字列からバイナリ（Uint8Array）に復元
            const binaryString = atob(savedDb);
            const binaryArray = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                binaryArray[i] = binaryString.charCodeAt(i);
            }
            db = new SQL.Database(binaryArray);
        } else {
            // 初めての起動なら、まっさらな状態でDBを作成してテーブルを作る
            db = new SQL.Database();
            createSchema();
        }
    } catch (e) {
        // 保存データが破損していた場合でも、詰み状態（起動不能）にしないためのフォールバック
        console.error('DBの復元に失敗したため、初期化します:', e);
        localStorage.removeItem(STORAGE_KEY);
        db = new SQL.Database();
        createSchema();
    }
}

function saveScore(score) {
    if (!db) return;
    const takenAt = new Date().toLocaleString('ja-JP');

    try {
        db.run(`INSERT INTO scores (score, taken_at) VALUES (?, ?);`, [score, takenAt]);

        // 現在のDBの状態を丸ごとBase64化してLocalStorageに保存
        // （JSON配列化より文字数を約1/3程度に圧縮できる）
        const exportedData = db.export(); // Uint8Array
        let binaryString = '';
        for (let i = 0; i < exportedData.length; i++) {
            binaryString += String.fromCharCode(exportedData[i]);
        }
        const base64Data = btoa(binaryString);
        localStorage.setItem(STORAGE_KEY, base64Data);
    } catch (e) {
        // 容量満杯・シークレットモード等で保存に失敗しても、アプリ自体は落とさない
        console.error('スコアの保存に失敗しました:', e);
    }
}

function getPastScores() {
    if (!db) return [];
    try {
        const result = db.exec(`SELECT score, taken_at FROM scores ORDER BY id DESC;`);
        if (result.length === 0) return [];
        return result[0].values.map(row => ({ score: row[0], takenAt: row[1] }));
    } catch (e) {
        console.error('履歴の取得に失敗しました:', e);
        return [];
    }
}
