// Serveur de stockage OBJET de la recette — SUBSTITUT DÉCLARÉ de MinIO.
// MinIO est IMPOSSIBLE à installer dans ce sandbox (constat mesuré, journalisé) :
//   * binaire de release : hôte de téléchargement injoignable ;
//   * conteneur : aucun registre joignable (et Docker/Podman absents) ;
//   * sources : pas de proxy de modules Go, ET le dépôt github.com/minio/console
//     (dépendance directe de cmd/) n'existe plus sur GitHub (404 vérifié) ;
//   * `go mod download` échoue de surcroît sur aead.dev/mem et aead.dev/minisign.
// s3rver est un VRAI serveur S3 (protocole HTTP, signature V4, stockage disque),
// mais ce n'est PAS MinIO : la recette l'étiquette comme substitut.
const S3rver = require("/home/user/rv/env/s3/node_modules/s3rver");
const AWSAccount = require("/home/user/rv/env/s3/node_modules/s3rver/lib/models/account.js");

for (const [cle, secret] of [
  [process.env.SEAMTECH_S3_ACCESS_KEY, process.env.SEAMTECH_S3_SECRET_KEY],
  [process.env.SEAMTECH_BACKUP_ACCESS_KEY, process.env.SEAMTECH_BACKUP_SECRET_KEY],
]) {
  if (cle && secret) AWSAccount.DUMMY_ACCOUNT.createKeyPair(cle, secret);
}

const serveur = new S3rver({
  port: Number(process.env.S3_PORT || 9000),
  address: "127.0.0.1", // écoute locale : le stockage n'est pas exposé au-delà du poste
  silent: false,
  directory: process.env.S3_DIRECTORY || "/home/user/rv/data/objets",
  key: process.env.SEAMTECH_S3_ACCESS_KEY,
  secret: process.env.SEAMTECH_S3_SECRET_KEY,
  configureBuckets: [{ name: process.env.SEAMTECH_S3_BUCKET || "seamtech-documents" }],
});

serveur.run((err) => {
  if (err) { console.error("stockage objet : démarrage impossible —", err.message); process.exit(1); }
  console.log("stockage objet (substitut MinIO, s3rver) à l'écoute sur http://127.0.0.1:9000");
});
