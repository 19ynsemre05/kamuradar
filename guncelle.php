<?php
// guncelle.php - Sayfa acilinca ilan_cek_web.py'yi arka planda kendisi calistirir.
// Sadece bu bilgisayardan (localhost) calisir. Komut penceresi acmaniz gerekmez.
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

// ---- AYARLAR ----
$AZAMI_YAS_DAKIKA = 15;      // Son BASARILI guncellemeden bu kadar dakika gectiyse yeniden calistir
$PYTHON = '';                // Bos birakirsan otomatik bulunur. Bulunamazsa tam yol yaz: '"C:\\Python312\\python.exe"'
// -----------------

if (!in_array($_SERVER['REMOTE_ADDR'] ?? '', ['127.0.0.1', '::1'], true)) {
    http_response_code(403);
    echo json_encode(['durum' => 'yasak']);
    exit;
}
require __DIR__ . DIRECTORY_SEPARATOR . 'ortak.php';

$dir   = __DIR__;
$kilit = $dir . DIRECTORY_SEPARATOR . 'guncelle.lock';
$son   = $dir . DIRECTORY_SEPARATOR . 'son_guncelleme.txt';   // sadece Python basariyla bitince yazilir
$log   = $dir . DIRECTORY_SEPARATOR . 'ilan_cek_log.txt';
$betik = $dir . DIRECTORY_SEPARATOR . 'ilan_cek_web.py';
$win   = (PHP_OS_FAMILY === 'Windows');

function calisiyor_mu($kilit) {
    return is_file($kilit) && (time() - filemtime($kilit)) < 30 * 60;   // 30 dk'dan eski kilit bozuk sayilir
}
function log_ozeti($log) {
    $eklenen = null;
    if (is_file($log) && preg_match('/Bitti\. Eklenen: (\d+)/', (string)file_get_contents($log), $m)) {
        $eklenen = (int)$m[1];
    }
    return $eklenen;
}
function hata($mesaj) {
    echo json_encode(['durum' => 'hata', 'mesaj' => $mesaj], JSON_UNESCAPED_UNICODE);
    exit;
}

$islem = $_GET['islem'] ?? 'durum';

if ($islem === 'durum') {
    echo json_encode([
        'durum'   => calisiyor_mu($kilit) ? 'calisiyor' : 'bitti',
        'eklenen' => log_ozeti($log),
        'son'     => is_file($son) ? (int)filemtime($son) : null,
    ]);
    exit;
}

// islem = baslat  (zorla=1 ise sure beklemeden)
if (calisiyor_mu($kilit)) {
    echo json_encode(['durum' => 'calisiyor', 'son' => is_file($son) ? (int)filemtime($son) : null]);
    exit;
}
$zorla = !empty($_GET['zorla']);
// Bir kaynak hata vermisse 15 dk beklemeden (en az 3 dk arayla) yeniden dener
$hatali = false;
$jf = $dir . DIRECTORY_SEPARATOR . 'ilanlar.json';
if (is_file($jf)) {
    $jd = json_decode((string)file_get_contents($jf), true);
    foreach (((is_array($jd) ? $jd['kaynaklar'] : null) ?? []) as $k) {
        if (($k['durum'] ?? '') === 'hata') { $hatali = true; }
    }
    if ((int)((is_array($jd) ? ($jd['surum'] ?? 0) : 0)) < 3) { $zorla = true; }   // eski surum: hemen yeniden cek
}
$bekle = $hatali ? 3 : $AZAMI_YAS_DAKIKA;
if (!$zorla && is_file($son) && (time() - filemtime($son)) < $bekle * 60) {
    echo json_encode(['durum' => 'taze', 'son' => (int)filemtime($son)]);
    exit;
}
if (!function_exists('popen') || !function_exists('exec')) {
    hata('PHP popen()/exec() kapali (php.ini disable_functions). Ayrinti: http://localhost/kamuradar/tani.php');
}
if (!is_writable($dir)) {
    hata('Klasore yazilamiyor: ' . $dir);
}

$py = kr_python_bul($PYTHON);
if ($py['cmd'] === null) {
    hata('Python bulunamadi (Windows Magaza kisayolu olabilir). Ayrinti: http://localhost/kamuradar/tani.php');
}
list($var, $_o) = kr_paket_var($py['cmd'], 'requests');
if (!$var) {
    hata('Python\'a requests paketi kurulu degil. Komut isteminde sunu calistir: ' . $py['cmd'] . ' -m pip install requests truststore');
}

file_put_contents($kilit, (string)time());
if ($win) {
    // Kucuk bir .bat yazip gorunmez pencerede calistirir; Python basariyla bitince son_guncelleme.txt yazilir.
    $bat = $dir . DIRECTORY_SEPARATOR . 'guncelle_calistir.bat';
    $satirlar = [
        '@echo off',
        'chcp 65001 >nul',
        'set PYTHONUTF8=1',
        'set PYTHONIOENCODING=utf-8',
        'cd /d "' . $dir . '"',
        $py['cmd'] . ' -m pip install --quiet --disable-pip-version-check pypdf >nul 2>&1',
        $py['cmd'] . ' "' . $betik . '" > "' . $log . '" 2>&1',
        'if not errorlevel 1 type nul > "' . $son . '"',
        'del "' . $kilit . '" 2>nul',
    ];
    file_put_contents($bat, implode("\r\n", $satirlar) . "\r\n");
    pclose(popen('start /B "" "' . $bat . '"', 'r'));
} else {
    $cmd = 'cd ' . escapeshellarg($dir) . ' && (' . $py['cmd'] . ' -m pip install --quiet pypdf >/dev/null 2>&1; PYTHONUTF8=1 ' . $py['cmd'] . ' ' . escapeshellarg($betik)
         . ' > ' . escapeshellarg($log) . ' 2>&1 && touch ' . escapeshellarg($son)
         . '; rm -f ' . escapeshellarg($kilit) . ') > /dev/null 2>&1 &';
    pclose(popen($cmd, 'r'));
}
echo json_encode(['durum' => 'basladi', 'son' => time()]);
