from base64 import b64encode
from ..utils import ensure_binary

import six
import warnings


try:
    import Cryptodome.Hash.SHA256 as Crypt_SHA256
    import Cryptodome.Hash.SHA384 as Crypt_SHA384
    import Cryptodome.Hash.SHA512 as Crypt_SHA512

    from Cryptodome.PublicKey import RSA
    from Cryptodome.Signature import PKCS1_v1_5
    from Cryptodome.Util.asn1 import DerSequence
    from Cryptodome.Cipher import AES, PKCS1_OAEP, PKCS1_v1_5 as Cipher_PKCS1_v1_5
    from Cryptodome.Random import get_random_bytes as crypto_get_random_bytes


except ImportError:
    import Crypto.Hash.SHA256 as Crypt_SHA256
    import Crypto.Hash.SHA384 as Crypt_SHA384
    import Crypto.Hash.SHA512 as Crypt_SHA512

    from Crypto.PublicKey import RSA
    from Crypto.Signature import PKCS1_v1_5
    from Crypto.Util.asn1 import DerSequence
    from Crypto.Cipher import AES, PKCS1_OAEP, PKCS1_v1_5 as Cipher_PKCS1_v1_5
    from Crypto.Random import get_random_bytes as crypto_get_random_bytes


from jose.backends.base import Key
from jose.backends._asn1 import rsa_public_key_pkcs8_to_pkcs1
from jose.utils import base64_to_long, long_to_base64
from jose.constants import ALGORITHMS
from jose.utils import base64url_decode
from ..exceptions import JWEError, JWKError

# We default to using PyCryptodome, however, if PyCrypto is installed, it is
# used instead. This is so that environments that require the use of PyCrypto
# are still supported.
if hasattr(RSA, 'RsaKey'):
    _RSAKey = RSA.RsaKey
else:
    _RSAKey = RSA._RSAobj


def _der_to_pem(der_key, marker):
    """
    Perform a simple DER to PEM conversion.
    """
    pem_key_chunks = [('-----BEGIN %s-----' % marker).encode('utf-8')]

    # Limit base64 output lines to 64 characters by limiting input lines to 48 characters.
    for chunk_start in range(0, len(der_key), 48):
        pem_key_chunks.append(b64encode(der_key[chunk_start:chunk_start + 48]))

    pem_key_chunks.append(('-----END %s-----' % marker).encode('utf-8'))

    return b'\n'.join(pem_key_chunks)

def get_random_bytes(num_bytes):
    """
    Get random bytes

    Args:
        num_bytes (int): Number of random bytes to generate and return
    Returns:
        bytes: Random bytes
    """
    return crypto_get_random_bytes(num_bytes)


class RSAKey(Key):
    """
    Performs signing and verification operations using
    RSASSA-PKCS-v1_5 and the specified hash function.
    This class requires PyCrypto package to be installed.
    This is based off of the implementation in PyJWT 0.3.2
    """

    SHA256 = Crypt_SHA256
    SHA384 = Crypt_SHA384
    SHA512 = Crypt_SHA512

    def __init__(self, key, algorithm):

        if algorithm not in ALGORITHMS.RSA:
            raise JWKError('hash_alg: %s is not a valid hash algorithm' % algorithm)

        self.hash_alg = {
            ALGORITHMS.RS256: self.SHA256,
            ALGORITHMS.RS384: self.SHA384,
            ALGORITHMS.RS512: self.SHA512
        }.get(algorithm)
        self._algorithm = algorithm

        if isinstance(key, _RSAKey):
            self.prepared_key = key

        if isinstance(key, dict):
            self._process_jwk(key)

        if isinstance(key, six.string_types):
            key = key.encode('utf-8')

        if isinstance(key, six.binary_type):
            if key.startswith(b'-----BEGIN CERTIFICATE-----'):
                try:
                    self._process_cert(key)
                except Exception as e:
                    raise JWKError(e)
                return

            try:
                self.prepared_key = RSA.importKey(key)
            except Exception as e:
                raise JWKError(e)

        if self.prepared_key:
            self.padding_cipher = {
                ALGORITHMS.RSA1_5: Cipher_PKCS1_v1_5.new(self.prepared_key),
                ALGORITHMS.RSA_OAEP: PKCS1_OAEP.new(self.prepared_key),
                ALGORITHMS.RSA_OAEP_256: PKCS1_OAEP.new(self.prepared_key, self.SHA256),
            }.get(algorithm)        
            return

        raise JWKError('Unable to parse an RSA_JWK from key: %s' % key)

    def _process_jwk(self, jwk_dict):
        if not jwk_dict.get('kty') == 'RSA':
            raise JWKError("Incorrect key type. Expected: 'RSA', Received: %s" % jwk_dict.get('kty'))

        e = base64_to_long(jwk_dict.get('e', 256))
        n = base64_to_long(jwk_dict.get('n'))
        params = (n, e)

        if 'd' in jwk_dict:
            params += (base64_to_long(jwk_dict.get('d')),)

            extra_params = ['p', 'q', 'dp', 'dq', 'qi']

            if any(k in jwk_dict for k in extra_params):
                # Precomputed private key parameters are available.
                if not all(k in jwk_dict for k in extra_params):
                    # These values must be present when 'p' is according to
                    # Section 6.3.2 of RFC7518, so if they are not we raise
                    # an error.
                    raise JWKError('Precomputed private key parameters are incomplete.')

                p = base64_to_long(jwk_dict.get('p'))
                q = base64_to_long(jwk_dict.get('q'))
                qi = base64_to_long(jwk_dict.get('qi'))

                # PyCrypto does not take the dp and dq as arguments, so we do
                # not pass them. Furthermore, the parameter qi specified in
                # the JWK is the inverse of q modulo p, whereas PyCrypto
                # takes the inverse of p modulo q. We therefore switch the
                # parameters to make the third parameter the inverse of the
                # second parameter modulo the first parameter.
                params += (q, p, qi)

        self.prepared_key = RSA.construct(params)

        return self.prepared_key

    def _process_cert(self, key):
        pemLines = key.replace(b' ', b'').split()
        certDer = base64url_decode(b''.join(pemLines[1:-1]))
        certSeq = DerSequence()
        certSeq.decode(certDer)
        tbsSeq = DerSequence()
        tbsSeq.decode(certSeq[0])
        self.prepared_key = RSA.importKey(tbsSeq[6])
        return

    def sign(self, msg):
        try:
            return PKCS1_v1_5.new(self.prepared_key).sign(self.hash_alg.new(msg))
        except Exception as e:
            raise JWKError(e)

    def verify(self, msg, sig):
        if not self.is_public():
            warnings.warn("Attempting to verify a message with a private key. "
                          "This is not recommended.")
        try:
            return PKCS1_v1_5.new(self.prepared_key).verify(self.hash_alg.new(msg), sig)
        except Exception:
            return False

    def is_public(self):
        return not self.prepared_key.has_private()

    def public_key(self):
        if self.is_public():
            return self
        return self.__class__(self.prepared_key.publickey(), self._algorithm)

    def to_pem(self, pem_format='PKCS8'):
        if pem_format == 'PKCS8':
            pkcs = 8
        elif pem_format == 'PKCS1':
            pkcs = 1
        else:
            raise ValueError("Invalid pem format specified: %r" % (pem_format,))

        if self.is_public():
            # PyCrypto/dome always export public keys as PKCS8
            if pkcs == 8:
                pem = self.prepared_key.exportKey('PEM')
            else:
                pkcs8_der = self.prepared_key.exportKey('DER')
                pkcs1_der = rsa_public_key_pkcs8_to_pkcs1(pkcs8_der)
                pem = _der_to_pem(pkcs1_der, 'RSA PUBLIC KEY')
            return pem
        else:
            pem = self.prepared_key.exportKey('PEM', pkcs=pkcs)
        return pem

    def to_dict(self):
        data = {
            'alg': self._algorithm,
            'kty': 'RSA',
            'n': long_to_base64(self.prepared_key.n).decode('ASCII'),
            'e': long_to_base64(self.prepared_key.e).decode('ASCII'),
        }

        if not self.is_public():
            # Section 6.3.2 of RFC7518 prescribes that when we include the
            # optional parameters p and q, we must also include the values of
            # dp and dq, which are not readily available from PyCrypto - so we
            # calculate them. Moreover, PyCrypto stores the inverse of p
            # modulo q rather than the inverse of q modulo p, so we switch
            # p and q. As far as I can tell, this is OK - RFC7518 only
            # asserts that p is the 'first factor', but does not specify
            # what 'first' means in this case.
            dp = self.prepared_key.d % (self.prepared_key.p - 1)
            dq = self.prepared_key.d % (self.prepared_key.q - 1)
            data.update({
                'd': long_to_base64(self.prepared_key.d).decode('ASCII'),
                'p': long_to_base64(self.prepared_key.q).decode('ASCII'),
                'q': long_to_base64(self.prepared_key.p).decode('ASCII'),
                'dp': long_to_base64(dq).decode('ASCII'),
                'dq': long_to_base64(dp).decode('ASCII'),
                'qi': long_to_base64(self.prepared_key.u).decode('ASCII'),
            })

        return data
    
    def wrap_key(self, key_data):
        try:
            wrapped_key = self.padding_cipher.encrypt(key_data)
        except Exception as e:
            raise JWEError(e)

        return wrapped_key

    def unwrap_key(self, wrapped_key):
        try:
            unwrapped_key = self.padding_cipher.decrypt(wrapped_key)
            return unwrapped_key
        except Exception as e:
            raise JWEError(e)
        
class AESKey(Key):
    KEY_128 = (ALGORITHMS.A128GCM, ALGORITHMS.A128GCMKW, ALGORITHMS.A128KW, ALGORITHMS.A128CBC)
    KEY_192 = (ALGORITHMS.A192GCM, ALGORITHMS.A192GCMKW, ALGORITHMS.A192KW, ALGORITHMS.A192CBC)
    KEY_256 = (
        ALGORITHMS.A256GCM,
        ALGORITHMS.A256GCMKW,
        ALGORITHMS.A256KW,
        ALGORITHMS.A128CBC_HS256,
        ALGORITHMS.A256CBC,
    )
    KEY_384 = (ALGORITHMS.A192CBC_HS384,)
    KEY_512 = (ALGORITHMS.A256CBC_HS512,)

    AES_KW_ALGS = (ALGORITHMS.A128KW, ALGORITHMS.A192KW, ALGORITHMS.A256KW)

    def __init__(self, key, algorithm):
        if algorithm not in ALGORITHMS.AES:
            raise JWKError("%s is not a valid AES algorithm" % algorithm)

        self._algorithm = algorithm

        if algorithm in self.KEY_128 and len(key) != 16:
            raise JWKError(f"Key must be 128 bit for alg {algorithm}")
        elif algorithm in self.KEY_192 and len(key) != 24:
            raise JWKError(f"Key must be 192 bit for alg {algorithm}")
        elif algorithm in self.KEY_256 and len(key) != 32:
            raise JWKError(f"Key must be 256 bit for alg {algorithm}")
        elif algorithm in self.KEY_384 and len(key) != 48:
            raise JWKError(f"Key must be 384 bit for alg {algorithm}")
        elif algorithm in self.KEY_512 and len(key) != 64:
            raise JWKError(f"Key must be 512 bit for alg {algorithm}")

        self._key = key

    def to_dict(self):
        data = {"alg": self._algorithm, "kty": "oct", "k": long_to_base64(int.from_bytes(self._key, "big")).decode("ASCII")}
        return data

    def encrypt(self, plain_text, aad=None):
        plain_text = ensure_binary(plain_text)
        try:
            if self._algorithm in (ALGORITHMS.A128GCM, ALGORITHMS.A192GCM, ALGORITHMS.A256GCM):
                iv = get_random_bytes(12)  # GCM standard IV size
                cipher = AES.new(self._key, AES.MODE_GCM, nonce=iv)
                if aad:
                    cipher.update(aad)
                cipher_text, auth_tag = cipher.encrypt_and_digest(plain_text)
            else:
                iv = get_random_bytes(16)  # CBC standard IV size
                cipher = AES.new(self._key, AES.MODE_CBC, iv=iv)
                padded_data = self._pkcs7_pad(plain_text)
                cipher_text = cipher.encrypt(padded_data)
                auth_tag = None
            return iv, cipher_text, auth_tag
        except Exception as e:
            raise JWEError(e)

    def decrypt(self, cipher_text, iv=None, aad=None, tag=None):
        cipher_text = ensure_binary(cipher_text)
        try:
            iv = ensure_binary(iv)
            if self._algorithm in (ALGORITHMS.A128GCM, ALGORITHMS.A192GCM, ALGORITHMS.A256GCM):
                if tag is None:
                    raise ValueError("tag cannot be None")
                cipher = AES.new(self._key, AES.MODE_GCM, nonce=iv)
                if aad:
                    cipher.update(aad)
                try:
                    plain_text = cipher.decrypt_and_verify(cipher_text, tag)
                except ValueError:
                    raise JWEError("Invalid JWE Auth Tag")
            else:
                cipher = AES.new(self._key, AES.MODE_CBC, iv=iv)
                padded_plain_text = cipher.decrypt(cipher_text)
                plain_text = self._pkcs7_unpad(padded_plain_text)
            return plain_text
        except Exception as e:
            raise JWEError(e)

    def wrap_key(self, key_data):
        key_data = ensure_binary(key_data)
        try:
            wrapped_key = AESKeyWrap(self._key).wrap(key_data)
            return wrapped_key
        except Exception as e:
            raise JWEError(e)

    def unwrap_key(self, wrapped_key):
        wrapped_key = ensure_binary(wrapped_key)
        try:
            unwrapped_key = AESKeyWrap(self._key).unwrap(wrapped_key)
            return unwrapped_key
        except Exception as e:
            raise JWEError(e)

    @staticmethod
    def _pkcs7_pad(data, block_size=16):
        pad_len = block_size - len(data) % block_size
        return data + bytes([pad_len] * pad_len)

    @staticmethod
    def _pkcs7_unpad(data, block_size=16):
        pad_len = data[-1]
        if pad_len < 1 or pad_len > block_size:
            raise ValueError("Invalid padding")
        return data[:-pad_len]
