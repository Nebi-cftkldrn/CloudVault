import os
import uuid
import certifi
from flask import Flask, render_template, request, redirect, url_for, Response, session, flash
import boto3
from dotenv import load_dotenv
from datetime import datetime
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash

# Çevresel değişkenleri yükle
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ==========================================
# ☁️ BULUT ALTYAPI BAĞLANTILARI
# ==========================================

# 1. AWS S3 Bağlantısı (IaaS)
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name='eu-central-1'
)
BUCKET_NAME = os.getenv('AWS_BUCKET_NAME')

# 2. MongoDB Atlas Bağlantısı (PaaS - SSL Koruması Aşılmış)
try:
    mongo_client = MongoClient(
        os.getenv('MONGO_URI'), 
        tlsAllowInvalidCertificates=True, 
        serverSelectionTimeoutMS=5000
    )
    db = mongo_client['CloudVaultSocialDB']
    kullanicilar_koleksiyonu = db['Kullanicilar']
    gonderiler_koleksiyonu = db['Gonderiler']
    print("🚀 Bulut Veritabanı (MongoDB) Bağlantısı Başarılı!")
except Exception as e:
    print(f"CRITICAL: Bağlantı Hatası: {e}")

# ==========================================
# 🌐 SAYFA ROTALARI (VİTRİN)
# ==========================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/login_page')
def login_page():
    if 'kullanici' in session:
        return redirect(url_for('dashboard'))
    return render_template('auth.html')

# ==========================================
# 🔐 KİMLİK DOĞRULAMA (AUTH)
# ==========================================

@app.route('/register', methods=['POST'])
def register():
    kullanici_adi = request.form.get('kullanici_adi', '').strip()
    email = request.form.get('email', '').strip()
    telefon = request.form.get('telefon', '').strip()
    sifre = request.form.get('sifre')
    
    if not kullanici_adi or not sifre:
        flash('Kullanıcı adı ve şifre zorunludur!', 'danger')
        return redirect(url_for('login_page'))
    
    if kullanicilar_koleksiyonu.find_one({"kullanici_adi": kullanici_adi}):
        flash('Bu kullanıcı adı zaten alınmış!', 'danger')
        return redirect(url_for('login_page'))
        
    kullanicilar_koleksiyonu.insert_one({
        "kullanici_adi": kullanici_adi,
        "sifre": generate_password_hash(sifre),
        "email": email,
        "telefon": telefon,
        "biyografi": "",
        "profil_fotografi": "",
        "kayit_tarihi": datetime.now().strftime('%d/%m/%Y %H:%M')
    })
    flash('Kayıt başarılı! Giriş yapabilirsiniz.', 'success')
    return redirect(url_for('login_page'))

@app.route('/login', methods=['POST'])
def login():
    kullanici_adi = request.form.get('kullanici_adi', '').strip()
    sifre = request.form.get('sifre')
    
    if not kullanici_adi or not sifre:
        flash('Lütfen tüm alanları doldurun!', 'danger')
        return redirect(url_for('login_page'))
        
    user = kullanicilar_koleksiyonu.find_one({"kullanici_adi": kullanici_adi})
    
    if user and check_password_hash(user['sifre'], sifre):
        session['kullanici'] = kullanici_adi
        return redirect(url_for('dashboard'))
        
    flash('Hatalı kullanıcı adı veya şifre!', 'danger')
    return redirect(url_for('login_page'))

@app.route('/logout')
def logout():
    session.pop('kullanici', None)
    return redirect(url_for('index'))

# ==========================================
# 📱 UYGULAMA İÇİ ROTALAR & BULUT İŞLEMLERİ
# ==========================================

@app.route('/dashboard')
def dashboard():
    if 'kullanici' not in session:
        return redirect(url_for('login_page'))
    
    # Tüm gönderileri tarihe göre yeniden eskiye sıralı çekiyoruz
    db_gonderileri = list(gonderiler_koleksiyonu.find().sort("tarih_iso", -1))
    
    # HER GÖNDERİ İÇİN: Sahibinin profil fotoğrafını Kullanıcılar'dan bulup eşleştir
    for dosya in db_gonderileri:
        kullanici_bilgisi = kullanicilar_koleksiyonu.find_one({"kullanici_adi": dosya["sahip"]})
        
        if kullanici_bilgisi and kullanici_bilgisi.get("profil_fotografi"):
            dosya["sahip_pp"] = kullanici_bilgisi["profil_fotografi"]
        else:
            dosya["sahip_pp"] = "" # Fotoğraf yoksa baş harfi gösterecek
            
    return render_template('dashboard.html', gonderiler=db_gonderileri)
@app.route('/profile')
def profile():
    if 'kullanici' not in session:
        return redirect(url_for('login_page'))
    
    aktif_kullanici = session['kullanici']
    user_data = kullanicilar_koleksiyonu.find_one({"kullanici_adi": aktif_kullanici})
    db_gonderileri = list(gonderiler_koleksiyonu.find({"sahip": aktif_kullanici}).sort("tarih_iso", -1))
    
    return render_template('profile.html', kullanici_adi=aktif_kullanici, user=user_data, dosyalar=db_gonderileri)

@app.route('/edit_profile', methods=['POST'])
def edit_profile():
    if 'kullanici' not in session: 
        return redirect(url_for('login_page'))
        
    aktif_kullanici = session['kullanici']
    email = request.form.get('email', '').strip()
    telefon = request.form.get('telefon', '').strip()
    biyografi = request.form.get('biyografi', '').strip()
    yeni_sifre = request.form.get('yeni_sifre')
    
    update_data = {
        "email": email,
        "telefon": telefon,
        "biyografi": biyografi
    }
    
    # Profil fotoğrafı yükleme altyapısı (AWS S3)
    file = request.files.get('profil_fotografi')
    if file and file.filename != '':
        uzanti = file.filename.split('.')[-1].lower()
        photo_name = f"pp_{aktif_kullanici}_{uuid.uuid4().hex[:8]}.{uzanti}"
        s3_client.upload_fileobj(file, BUCKET_NAME, photo_name, ExtraArgs={'ContentType': file.content_type})
        update_data["profil_fotografi"] = photo_name
        
    if yeni_sifre and yeni_sifre.strip() != '':
        update_data["sifre"] = generate_password_hash(yeni_sifre)
        
    kullanicilar_koleksiyonu.update_one(
        {"kullanici_adi": aktif_kullanici},
        {"$set": update_data}
    )
    flash('Profil başarıyla güncellendi!', 'success')
    return redirect(url_for('profile'))

@app.route('/upload', methods=['POST'])
def upload():
    if 'kullanici' not in session: 
        return redirect(url_for('login_page'))
        
    file = request.files.get('dosya')
    aciklama = request.form.get('aciklama', '').strip()
    
    if file and file.filename != '':
        uzanti = file.filename.split('.')[-1].lower()
        benzersiz_isim = f"post_{session['kullanici']}_{uuid.uuid4().hex[:8]}.{uzanti}"
        
        file.seek(0, os.SEEK_END)
        boyut_kb = round(file.tell() / 1024, 2)
        file.seek(0)
        
        # 1. Ham Dosyayı AWS S3'e Yükle
        s3_client.upload_fileobj(file, BUCKET_NAME, benzersiz_isim, ExtraArgs={'ContentType': file.content_type})
        
        # 2. Meta Veriyi MongoDB'ye Kaydet
        gonderiler_koleksiyonu.insert_one({
            "id": benzersiz_isim,
            "sahip": session['kullanici'],
            "gosterilen_isim": file.filename,
            "aciklama": aciklama,
            "boyut": boyut_kb,
            "tarih_gosterim": datetime.now().strftime('%d/%m/%Y %H:%M'),
            "tarih_iso": datetime.now().isoformat()
        })
    return redirect(url_for('dashboard'))

@app.route('/view/<filename>')
def view(filename):
    try:
        obj = s3_client.get_object(Bucket=BUCKET_NAME, Key=filename)
        return Response(obj['Body'].read(), mimetype=obj['ContentType'])
    except Exception:
        return "Görüntülenemedi", 404

@app.route('/download/<filename>')
def download(filename):
    try:
        obj = s3_client.get_object(Bucket=BUCKET_NAME, Key=filename)
        return Response(
            obj['Body'].read(),
            mimetype=obj['ContentType'],
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception:
        return "İndirme hatası", 404

@app.route('/delete/<filename>')
def delete(filename):
    if 'kullanici' not in session: 
        return redirect(url_for('login_page'))
    try:
        # Hem S3'ten hem de MongoDB'den Sil
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=filename)
        gonderiler_koleksiyonu.delete_one({"id": filename})
    except Exception:
        pass
    return redirect(request.referrer or url_for('dashboard'))

# ----------------------------------------------------
# SOSYAL ETKİLEŞİM ROTALARI
# ----------------------------------------------------

@app.route('/like/<post_id>', methods=['POST'])
def like(post_id):
    if 'kullanici' not in session: return redirect(url_for('login_page'))
    
    # Gönderiyi bul
    gonderi = gonderiler_koleksiyonu.find_one({"id": post_id})
    
    # Eğer kullanıcı zaten beğendiyse beğeniyi geri al (pull), beğenmediyse ekle (addToSet)
    if session['kullanici'] in gonderi.get('begenenler', []):
        gonderiler_koleksiyonu.update_one({"id": post_id}, {"$pull": {"begenenler": session['kullanici']}})
    else:
        gonderiler_koleksiyonu.update_one({"id": post_id}, {"$addToSet": {"begenenler": session['kullanici']}})
        
    return redirect(request.referrer) # İşlem bitince kaldığı sayfada bırakır

@app.route('/comment/<post_id>', methods=['POST'])
def comment(post_id):
    if 'kullanici' not in session: return redirect(url_for('login_page'))
    
    yorum_metni = request.form.get('yorum_metni', '').strip()
    if yorum_metni:
        yeni_yorum = {
            "kullanici": session['kullanici'],
            "metin": yorum_metni,
            "tarih": datetime.now().strftime('%d/%m/%Y %H:%M')
        }
        gonderiler_koleksiyonu.update_one({"id": post_id}, {"$push": {"yorumlar": yeni_yorum}})
        
    return redirect(request.referrer)

if __name__ == '__main__':
    app.run(debug=True)