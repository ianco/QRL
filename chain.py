#QRL main blockchain, state, stake, transaction functions.

# todo:
# pos_block_pool() should return all combinations, not just the order received then sorted by txhash - removes edge cases for block selection failure..
# add stake list check to the state check - addresses which are staking cannot make transactions..
# block-reward calculation to be altered based upon block-time and stake_list_get() balances..proportion of entire coin supply..
# fees
# occasionally the ots index gets behind..find reason..
# add salt/key xor to hash chains..

__author__ = 'pete'

import ntp
from merkle import sha256, numlist
from time import time, sleep
from operator import itemgetter
from math import log, ceil

import os, copy, ast, sys, json, jsonpickle, decimal
from collections import defaultdict
import merkle, wallet, db

import cPickle as pickle
import common


global transaction_pool, stake_pool, txhash_timestamp, m_blockchain, my, node_list, ping_list, last_ping, recent_blocks, pos_d, pos_flag, ip_list, blockheight_map, pos_consensus

global mining_address, stake_list, stake_commit, stake_reveal, hash_chain, epoch_prf, epoch_PRF, tx_per_block, stake_reveal_one, stake_reveal_two, stake_reveal_three, expected_winner

version_number = "alpha/0.08a"
minimum_required_stakers = 5
tx_per_block = [0, 0]
ping_list =[]
node_list = [common.ROOT_NODE()]
m_blockchain = []
transaction_pool = []
prev_txpool = [None]*1000
pending_tx_pool = []
pending_tx_pool_hash = []
txhash_timestamp = []
stake_commit = []
stake_reveal = []
stake_reveal_one = []
stake_reveal_two = []
stake_reveal_three = []
last_stake_reveal_one = []
last_stake_reveal_two = []
last_stake_reveal_three = []
stake_ban_list = []
stake_ban_block = {}
stake_pool = []
stake_list = []
epoch_prf = []
epoch_PRF = []
expected_winner = []
recent_blocks = []
pos_d = []
pos_consensus = []
pos_flag = []
ip_list = []
blockheight_map = []
stake_validator_latency = defaultdict(dict)

ntp.printL = printL
printL(( 'QRL blockchain ledger v', version_number))
printL(( 'loading db'))
db = db.DB()

printL(( 'loading wallet'))
my = wallet.f_read_wallet()
wallet.f_load_winfo()
mining_address = my[0][1].address
printL(( 'mining/staking address', mining_address))
#hash_chain = my[0][1].hc

# pos

def validate_block_timestamp(timestamp, blocknumber):
	last_block_timestamp = m_blockchain[-1].blockheader.timestamp
	if last_block_timestamp>=timestamp:
		return False
	curr_ntp = ntp.getNTP()
	if curr_ntp == 0:
		return False
	block_creation_second = 55
	
	max_block_number = int((curr_ntp - last_block_timestamp)/(1000*block_creation_second))
	if blocknumber > max_block_number:
		return False

def validate_reboot(hash, nonce):
	reboot_data = ['2920c8ec34f04f59b7df4284a4b41ca8cbec82ccdde331dd2d64cc89156af653', 0]
	try:
		reboot_data = db.get('reboot_data')
	except:
		pass
	if reboot_data[1]>nonce:	#already used
		return 
	reboot_data[1] = nonce
	output = hash
	for i in range(0, reboot_data[1]):
		output = sha256(output)

	if output != hash:
		return False
	reboot_data[1] += 1
	db.put('reboot_data', reboot_data)
	return True

def generate_reboot_hash(key, nonce=None):		
	reboot_data = ['2920c8ec34f04f59b7df4284a4b41ca8cbec82ccdde331dd2d64cc89156af653', 0]
	try:
		reboot_data = db.get('reboot_data')
	except:
		pass
	if nonce:
		if reboot_data[1]>nonce:
			return None, 'Nonce must be greater than or equals to '+str(reboot_data[1])+'\r\n'
		reboot_data[1] = nonce
	
	output = sha256(key)
	for i in range(0, 40000-reboot_data[1]):
		output = sha256(output)

	if not validate_reboot(output, reboot_data[1]):
		return None, 'Invalid Key\r\n'

	return json.dumps({'hash':output, 'nonce':reboot_data[1]}), "Reboot Initiated\r\n"

def update_pending_tx_pool(tx, peer):
	if len(pending_tx_pool)>=common.EPOCH_SIZE():
		del pending_tx_pool[0]
		del pending_tx_pool_hash[0]
	pending_tx_pool.append([tx, peer])
	pending_tx_pool_hash.append(tx.txhash)
	
# create a block from a list of supplied tx_hashes, check state to ensure validity..

def create_stake_block(tx_hash_list, hashchain_hash, reveal_list=None):

	# full memory copy of the transaction pool..

	global transaction_pool

	t_pool2 = copy.deepcopy(transaction_pool)

	del transaction_pool[:]

	# recreate the transaction pool as in the tx_hash_list, ordered by txhash..

	d = []

	for tx in tx_hash_list:
		for t in t_pool2:
			if tx == t.txhash:
				d.append(t.txfrom)
				transaction_pool.append(t)
				t.nonce = state_nonce(t.txfrom)+d.count(t.txfrom)

	# create the block..

	block_obj = m_create_block(hashchain_hash, reveal_list)

	# reset the pool back

	transaction_pool = copy.deepcopy(t_pool2)

	return block_obj

# return a sorted list of txhashes from transaction_pool, sorted by timestamp from block n (actually from start of transaction_pool) to time, then ordered by txhash.

def sorted_tx_pool(timestamp=None):
	if timestamp == None:
		timestamp=time()
	pool = copy.deepcopy(transaction_pool)
	trimmed_pool = []
	end_time = timestamp
	for tx in pool:
		#if txhash_timestamp[txhash_timestamp.index(tx.txhash)+1] >= start_time and txhash_timestamp[txhash_timestamp.index(tx.txhash)+1] <= end_time:
		if txhash_timestamp[txhash_timestamp.index(tx.txhash)+1] <= end_time:
					trimmed_pool.append(tx.txhash)

	trimmed_pool.sort()

	if trimmed_pool == []:
		return False

	return trimmed_pool

# merkle tree root hash of tx from pool for next POS block

def merkle_tx_hash(hashes):
	#printL(( 'type', type(hashes), 'len', len(hashes)
	if len(hashes)==64:					# if len = 64 then it is a single hash string rather than a list..
		return hashes
	j=int(ceil(log(len(hashes),2)))
	l_array = []
	l_array.append(hashes)
	for x in range(j):
		next_layer = []
		i = len(l_array[x])%2 + len(l_array[x])/2
		z=0
		for y in range(i):
			if len(l_array[x])==z+1:
				next_layer.append(l_array[x][z])
			else:
				next_layer.append(sha256(l_array[x][z]+l_array[x][z+1]))
			z+=2
		l_array.append(next_layer)
	#printL(( l_array
	return ''.join(l_array[-1])

# return closest hash in numerical terms to merkle root hash of all the supplied hashes..

def closest_hash(list_hash):
	#printL(( 'list_hash', list_hash, len(list_hash)

	if type(list_hash) == list:
		if len(list_hash)==1:
			return False, False
	if type(list_hash)== str:
		if len(list_hash)==64:
			return False, False

	list_hash.sort()

	root = merkle_tx_hash(list_hash)

	p = []
	for l in list_hash:
		p.append(int(l,16))

	closest = cl(int(root,16),p)

	return ''.join(list_hash[p.index(closest)]), root


# return closest number in a hexlified list

def cl_hex(one, many):

	p = []
	for l in many:
		p.append(int(l,16))

	return many[p.index(cl(int(one,16), p))]


# return closest number in a list..

def cl(one,many):
	return min(many, key=lambda x:abs(x-one))


def is_stake_banned(stake_address):
	if stake_address in stake_ban_list:
		epoch_diff = (m_blockheight()/common.EPOCH_SIZE()) - (stake_ban_block[stake_address]/common.EPOCH_SIZE())
		if m_blockheight() - stake_ban_block[stake_address] > 10 or epoch_diff > 0:
			printL (( 'Stake removed from ban list' ))
			del stake_ban_block[stake_address]
			stake_ban_list.remove(stake_address)
			return False
		return True
		
	return False
			
def ban_stake(stake_address):
	printL (('stake address ', stake_address, ' added to block list' ))
	stake_ban_list.append(stake_address)
	stake_ban_block[stake_address] = m_blockheight()+1


# create a snapshot of the transaction pool to account for network traversal time (probably less than 300ms, but let's give a window of 1.5 seconds). 
# returns: list of merkle root hashes of the tx pool over last 1.5 seconds

#import itertools
# itertools.permutations([1, 2, 3])

def pos_block_pool(n=1.5):
	timestamp = time()
	start_time = timestamp-n

	x = sorted_tx_pool(start_time)
	y = sorted_tx_pool(timestamp)
	if y == False:				# if pool is empty -> return sha256 null
		return [sha256('')], [[]]
	elif x == y:					# if the pool isnt empty but there is no difference then return the only merkle hash possible..			
		return [merkle_tx_hash(y)], [y]
	else:						# there is a difference in contents of pool over last 1.5 seconds..
		merkle_hashes = []
		txhashes = []
		if x == False:				
			merkle_hashes.append(sha256(''))
			x = []
			txhashes.append(x)
		else:
			merkle_hashes.append(merkle_tx_hash(x))
			txhashes.append(x)
		tmp_txhashes = x

		for tx in reversed(transaction_pool):
			if tx.txhash in y and tx.txhash not in x:
				tmp_txhashes.append(tx.txhash)
				tmp_txhashes.sort()
				merkle_hashes.append(merkle_tx_hash(tmp_txhashes))
				txhashes.append(tmp_txhashes)

		return merkle_hashes, txhashes	

# create the PRF selector sequence based upon a seed and number of stakers in list (temporary..there are better ways to do this with bigger seed value, but it works)

def pos_block_selector(seed, n):
	n_bits = int(ceil(log(n,2)))
	prf = merkle.GEN_range_bin(seed, 1, 20000,1)
	prf_range = []
	for z in prf:
		x = ord(z) >> 8-n_bits
		if x < n:
			prf_range.append(x)
	return prf_range

# return the POS staker list position for given seed at index, i

def pos_block_selector_n(seed, n, i):
	l = pos_block_selector(seed, n)
	return l[i]

#A base class to be inherited by all other transaction
class Transaction():
	def __init__(self):
		pass

	def process_XMSS(self, type, txfrom, txhash):
		self.txfrom = txfrom
		self.txhash = txhash
		data = my[0][1]
		S = data.SIGN(self.txhash)				# Sig = {i, s, auth_route, i_bms, self.pk(i), self.PK_short}
		self.i = S[0]
		self.signature = S[1]
		self.merkle_path = S[2]
		self.i_bms = S[3]
		self.pub = S[4]
		self.PK = S[5]
		self.type = type
	
	def json_to_transaction(self, json_str_tx):
		self.dict_to_transaction(json.loads(json_str_tx))
		return self

	def dict_to_transaction(self, dict_tx):
		self.__dict__ = dict_tx
		return self

	def transaction_to_json(self):
		return json.dumps(self.__dict__)

#classes
class StakeTransaction(Transaction):
	def __init__(self):
		Transaction.__init__(self)

	def create_stake_transaction(self, hashchain_terminator=None):
		self.epoch = (m_blockchain[-1].blockheader.blocknumber)/common.EPOCH_SIZE()	#in this block the epoch is..
		if hashchain_terminator == None:
			self.hash = my[0][1].hashchain_reveal(epoch=self.epoch+1)[-1]
		else:
			self.hash = hashchain_terminator
		self.process_XMSS('ST', mining_address, self.hash) #self.hash to be replaced with self.txhash
		return self

	def validate_tx(self):
		if self.type != 'ST':
			return False

		if merkle.xmss_verify(self.hash, [self.i, self.signature, self.merkle_path, self.i_bms, self.pub, self.PK]) is False:
				return False
		if xmss_checkaddress(self.PK, self.txfrom) is False:
				return False

		return True

	def state_validate_tx(self):
		if self.type != 'ST':
			return False
		pub = self.pub
		pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]
		pubhash = sha256(''.join(pub))

		if pubhash in state_pubhash(self.txfrom):
			printL(( 'State validation failed for', self.hash, 'because: OTS Public key re-use detected'))
			return False

		return True

class SimpleTransaction(Transaction): 			#creates a transaction python class object which can be jsonpickled and sent into the p2p network..
	def __init__(self): # nonce removed..
		Transaction.__init__(self)
	
	def pre_condition(self):
		if state_uptodate() is False:
			printL(( 'Warning state not updated to allow safe tx validation, tx validity could be unreliable..'))
			#return False

		if state_balance(self.txfrom) is 0:
			printL(( 'State validation failed for', self.txhash, 'because: Empty address'))
			return False 

		if state_balance(self.txfrom) < self.amount: 
			printL(( 'State validation failed for', self.txhash,  'because: Insufficient funds'))
			return False
		return True
		
	def create_simple_transaction(self, txfrom, txto, amount, data, fee=0, hrs=''):
		self.txfrom = txfrom
		self.nonce = 0
		self.txto = txto
		self.amount = int(amount)
		self.fee = int(fee)
		self.ots_key = data.index

		pub = data.pk()
		pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]
		self.pubhash = sha256(''.join(pub))
		self.txhash = sha256(''.join(self.txfrom+str(self.pubhash)+self.txto+str(self.amount)+str(self.fee)))	
		self.merkle_root = data.root
		if not self.pre_condition():
			return False
		#self.verify = data.VERIFY(self.txhash, S)			#not necessary, to save time could be commented out to reduce tx signing speed..
		self.process_XMSS('TX', txfrom, self.txhash)
		
		return self

	def validate_tx(self, new=0):
		#cryptographic checks
		if self.txhash != sha256(''.join(self.txfrom+str(self.pubhash))+self.txto+str(self.amount)+str(self.fee)):
			return False

		# SIG is a list composed of: i, s, auth_route, i_bms, pk[i], PK
		if self.type != 'TX':
			return False
		
		if merkle.xmss_verify(self.txhash, [self.i, self.signature, self.merkle_path, self.i_bms, self.pub, self.PK]) is False:
			return False
		if xmss_checkaddress(self.PK, self.txfrom) is False:
			return False
		
		return True		

	def state_validate_tx(self):		#checks new tx validity based upon node statedb and node mempool. 

		if not self.pre_condition():
			return False

		pub = self.pub
		if self.type != 'TX':
			return False

		pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]
		
		pubhash = sha256(''.join(pub))

		for txn in transaction_pool:
			if txn.txhash == self.txhash:
				continue
			pub = txn.pub
			if txn.type != 'TX':
				return False
			pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]

			pubhashn = sha256(''.join(pub))

			if pubhashn == pubhash:
				printL(( 'State validation failed for', self.txhash, 'because: OTS Public key re-use detected'))
				return False

		if pubhash in state_pubhash(self.txfrom):
			printL(( 'State validation failed for', self.txhash, 'because: OTS Public key re-use detected'))
			return False

		return True
		
def creategenesisblock():
	return CreateGenesisBlock()

class BlockHeader():
	def __init__(self, blocknumber, hashchain_link, prev_blockheaderhash, number_transactions, hashedtransactions, number_stake, hashedstake, reveal_list=None):
		self.blocknumber = blocknumber
		self.hash = hashchain_link
		if self.blocknumber == 0:
			self.timestamp = 0
		else:
			self.timestamp = ntp.getNTP()
			if self.timestamp == 0:
				printL (( 'Failed to get NTP timestamp' ))
				return
		self.prev_blockheaderhash = prev_blockheaderhash
		self.number_transactions = number_transactions
		self.merkle_root_tx_hash = hashedtransactions
		self.number_stake = number_stake
		self.hashedstake = hashedstake
		if self.blocknumber == 0:
			self.reveal_list = []
			self.stake_selector = ''
			self.stake_nonce = 0
			self.block_reward = 0
			self.epoch = 0
		elif self.blocknumber ==1:
			self.reveal_list = []
			self.stake_nonce = common.EPOCH_SIZE()-hash_chain.index(hashchain_link)			
			self.epoch = (m_blockchain[-1].blockheader.blocknumber+1)/common.EPOCH_SIZE()			#need to add in logic for epoch stake_list recalculation..
			self.stake_selector = mining_address
			self.block_reward = block_reward(self.blocknumber)
		else:
			self.reveal_list = reveal_list
			for s in stake_list_get():
				if s[0] == mining_address:
					self.stake_nonce = s[2]+1
			self.epoch = (m_blockchain[-1].blockheader.blocknumber+1)/common.EPOCH_SIZE()			#need to add in logic for epoch stake_list recalculation..
			self.stake_selector = mining_address
			self.block_reward = block_reward(self.blocknumber)
		self.headerhash = sha256(self.stake_selector+str(self.epoch)+str(self.stake_nonce)+str(self.block_reward)+str(self.timestamp)+self.hash+str(self.blocknumber)+self.prev_blockheaderhash+str(self.number_transactions)+self.merkle_root_tx_hash+str(self.number_stake)+self.hashedstake)




class CreateBlock():
	def __init__(self, hashchain_link, reveal_list=None):
		#difficulty = 232
		data = m_get_last_block()
		lastblocknumber = data.blockheader.blocknumber
		prev_blockheaderhash = data.blockheader.headerhash
		if len(transaction_pool) == 0:
			hashedtransactions = sha256('')
		else:
			hashedtransactions = []
			for transaction in transaction_pool:
				hashedtransactions.append(transaction.txhash)
		hashedtransactions = merkle_tx_hash(hashedtransactions)
		self.transactions = []
		for tx in transaction_pool:
			self.transactions.append(tx)						#copy memory rather than sym link
		curr_epoch = (m_blockchain[-1].blockheader.blocknumber)/common.EPOCH_SIZE()
		if not stake_pool:
			hashedstake = sha256('')
		else:
			sthashes = []
			for st in stake_pool:
				if st.epoch != curr_epoch:
					printL (( 'Skipping st as epoch mismatch, CreateBlock()' ))
					printL (( 'Expected st epoch : ', curr_epoch ))
					printL (( 'Found st epoch : ', st.epoch )) 
					continue
				sthashes.append(st.hash)
			hashedstake = sha256(''.join(sthashes))
		self.stake = []
		for st in stake_pool:
			if st.epoch != curr_epoch:
				printL (( 'Skipping st as epoch mismatch, CreateBlock()' ))
				printL (( 'Expected st epoch : ', curr_epoch ))
				printL (( 'Found st epoch : ', st.epoch )) 

				continue
			self.stake.append(st)

		self.blockheader = BlockHeader(blocknumber=lastblocknumber+1, reveal_list=reveal_list, hashchain_link=hashchain_link, prev_blockheaderhash=prev_blockheaderhash, number_transactions=len(transaction_pool), hashedtransactions=hashedtransactions, number_stake=len(stake_pool), hashedstake=hashedstake)
		if self.blockheader.timestamp == 0:
			printL(( 'Failed to create block due to timestamp 0' ))


class CreateGenesisBlock():			#first block has no previous header to reference..
	def __init__(self):
		self.blockheader = BlockHeader(blocknumber=0, hashchain_link='genesis', prev_blockheaderhash=sha256('quantum resistant ledger'),number_transactions=0, hashedtransactions=sha256('0'), number_stake=0, hashedstake=sha256('0'))
		self.transactions = []
		self.stake = []
		self.state = [['Q8eb5a51d4b8a4b53c5b8b9ded93d7fd7717796c3690bee0767b1dcaadd3520c1e242', [0, 100000*100000000, []]] , ['Q44328abd64300ce05c7b297f13ff9f02de4c0e40c5956a2168dd8c0a7c476bf613ce',[0, 10000*100000000,[]]], ['Q4acc55bb7126f532cc1566242809153bb3cc8d360256aa94b7180ca4f7ffa555de57', [0, 10000*100000000,[]]], ['Q34eabf7ef2c6582096a433237a603b862fd5a70ac4efe4fd69faae21ca390512b3ac', [0, 10000*100000000,[]]], ['Qfc6a9b751915048a7888b65e77f9a248379d8b47c94081b3baced7c1234dc7f4b419', [0, 10000*100000000,[]]],
['Q816e7f9908b434f3b5ea629b99fe29aaeaa4369d15566d6a35fb757cae0521bccbb2', [0, 150000*100000000,[]]],
['Qd434eb60f72cc25e4a2e263ecf97c21eb234d885ad840db3540d23190bf41c8bc122', [0, 150000*100000000,[]]],
['Q54c6a375aa571a0fd12e69daf225bad1e32da34ad9cd8cf642b665c72dfdfbdbdb4c', [0, 150000*100000000,[]]],
['Q1165f76f6a56367c311ea47074ea313406dbf637ef7810315ec66736d0f2d82dd168', [0, 150000*100000000,[]]],
['Qea190a2e321e94b4a9742be41d6281870e5d5297183a36edc940ebeff685d3413fd1', [0, 150000*100000000,[]]] ]
		#Qfc6a9b751915048a7888b65e77f9a248379d8b47c94081b3baced7c1234dc7f4b419 petal
		#Q4acc55bb7126f532cc1566242809153bb3cc8d360256aa94b7180ca4f7ffa555de57 tiddler
		#Q8eb5a51d4b8a4b53c5b8b9ded93d7fd7717796c3690bee0767b1dcaadd3520c1e242 twiglet
		#Q44328abd64300ce05c7b297f13ff9f02de4c0e40c5956a2168dd8c0a7c476bf613ce bean
		#Qa1f6f52ecb490cc3209a5e3580aa9539edfff0cb5a3ce06adc8f0c1e46bf38bfe0a0 flea
		#self.stake_list = ['Q0815e965f3f51740fe3ea03ed5ffcefc90be932f68f8e29d8b792b10ddfb95113167','Q287814bf7fc151fbbda6e4e613cca6da0f04f80c4ebd4ab59352d44d5e5fc2fe95f3','Qcdfe2d4eb5dd71d49b24bf73301de767936af38fbf640385c347aa398a5a1f777aee']
		self.stake_list = ['Q8eb5a51d4b8a4b53c5b8b9ded93d7fd7717796c3690bee0767b1dcaadd3520c1e242', 'Qfc6a9b751915048a7888b65e77f9a248379d8b47c94081b3baced7c1234dc7f4b419',
'Q4acc55bb7126f532cc1566242809153bb3cc8d360256aa94b7180ca4f7ffa555de57',
'Q44328abd64300ce05c7b297f13ff9f02de4c0e40c5956a2168dd8c0a7c476bf613ce',
'Qa1f6f52ecb490cc3209a5e3580aa9539edfff0cb5a3ce06adc8f0c1e46bf38bfe0a0',
'Q816e7f9908b434f3b5ea629b99fe29aaeaa4369d15566d6a35fb757cae0521bccbb2',
'Qd434eb60f72cc25e4a2e263ecf97c21eb234d885ad840db3540d23190bf41c8bc122',
'Q54c6a375aa571a0fd12e69daf225bad1e32da34ad9cd8cf642b665c72dfdfbdbdb4c',
'Q1165f76f6a56367c311ea47074ea313406dbf637ef7810315ec66736d0f2d82dd168',
'Qea190a2e321e94b4a9742be41d6281870e5d5297183a36edc940ebeff685d3413fd1']
		#self.stake_list = ['Q775a5868cda4488f97436d1c7f45ddae68e896218dfe42f6233f46a72eebdb038066', 'Qe1563a15fe6ffae964473d11180aaace207bcb1ed1ac570dfb46684421f7bb4f10eb']
		self.stake_seed = '1a02aa2cbe25c60f491aeb03131976be2f9b5e9d0bc6b6d9e0e7c7fd19c8a076c29e028f5f3924b4'


class ReCreateBlock():						#recreate block class from JSON variables for processing
	def __init__(self, json_block):
		self.blockheader = ReCreateBlockHeader(json_block['blockheader'])
	
		transactions = json_block['transactions']
		self.transactions = []
		for tx in transactions:
			self.transactions.append(SimpleTransaction().dict_to_transaction(tx))

		stake = json_block['stake']
		self.stake = []
		
		for st in stake:
			st_obj = StakeTransaction().dict_to_transaction(st)
			if st_obj.epoch != self.blockheader.epoch:
				continue
			self.stake.append(st_obj)

class ReCreateBlockHeader():
	def __init__(self, json_blockheader):
		rl = json_blockheader['reveal_list']
		self.reveal_list = []
		for r in rl:								
				self.reveal_list.append(r.encode('latin1'))		
		self.stake_nonce = json_blockheader['stake_nonce']
		self.epoch = json_blockheader['epoch']
		self.headerhash = json_blockheader['headerhash'].encode('latin1')
		self.number_transactions = json_blockheader['number_transactions']
		self.number_stake = json_blockheader['number_stake']
		self.hash = json_blockheader['hash'].encode('latin1')
		self.timestamp = json_blockheader['timestamp']
		self.merkle_root_tx_hash = json_blockheader['merkle_root_tx_hash'].encode('latin1')
		self.hashedstake = json_blockheader['hashedstake'].encode('latin1')
		self.blocknumber = json_blockheader['blocknumber']
		self.prev_blockheaderhash = json_blockheader['prev_blockheaderhash'].encode('latin1')
		self.stake_selector = json_blockheader['stake_selector'].encode('latin1')
		self.block_reward = json_blockheader['block_reward']

# address functions

# for xmss

def xmss_rootoaddr(PK_short):
	return 'Q'+sha256(PK_short[0]+PK_short[1])+sha256(sha256(PK_short[0]+PK_short[1]))[:4]

def xmss_checkaddress(PK_short, address):
	if 'Q'+sha256(PK_short[0]+PK_short[1])+sha256(sha256(PK_short[0]+PK_short[1]))[:4] == address:
		return True
	return False

# for mss

def roottoaddr(merkle_root):
	return 'Q'+sha256(merkle_root)+sha256(sha256(merkle_root))[:4]

def checkaddress(merkle_root, address):
	if 'Q'+sha256(merkle_root)+sha256(sha256(merkle_root))[:4] == address:
		return True
	return False

# block reward calculation
# decay curve: 200 years (until 2217AD, 420480000 blocks at 15s block-times)
# N_tot is less the initial coin supply.

def calc_coeff(N_tot, block_tot):
	# lambda = Ln N_0 - Ln (N(t)) / t
	return log(N_tot)/block_tot

# calculate remaining emission at block_n: N=total initial coin supply, coeff = decay constant
# need to use decimal as floating point not precise enough on different platforms..

#def remaining_emission(N_tot,block_n):
#	coeff = calc_coeff(21000000, 420480000)
	# N_t = N_0.e^{-coeff.t} where t = block
#	return N_tot*e**(-coeff*block_n)

def remaining_emission(N_tot, block_n):
	coeff = calc_coeff(21000000, 420480000)
	return decimal.Decimal(N_tot*decimal.Decimal(-coeff*block_n).exp()).quantize(decimal.Decimal('1.00000000'), rounding=decimal.ROUND_HALF_UP)

# return block reward for the block_n 

def block_reward(block_n):
	return int((remaining_emission(21000000, block_n-1)-remaining_emission(21000000, block_n))*100000000)

# network serialising functions

def json_decode_st(json_tx):
	return ReCreateStakeTransaction(json.loads(json_tx))

def json_decode_tx(json_tx):										#recreate transaction class object safely 
	return ReCreateSimpleTransaction(json.loads(json_tx))

def json_decode_block(json_block):
	return ReCreateBlock(json.loads(json_block))

def json_encode(obj):
	return json.dumps(obj)

def json_decode(js_obj):
	return json.loads(js_obj)

class ComplexEncoder(json.JSONEncoder):
	def default(self, obj):
		return obj.__dict__
	
def json_bytestream(obj):	
	return json.dumps(obj.__dict__, cls=ComplexEncoder)

def json_bytestream_tx(tx_obj):											#JSON serialise tx object
	return json_bytestream(tx_obj)

def json_bytestream_pb(block_obj):
	return json_bytestream(block_obj)

def json_bytestream_ph(mini_block):
	return json_encode(mini_block)

def json_bytestream_bk(block_obj):										# "" block object
	return json_bytestream(block_obj)

def json_print(obj):													#prettify output from JSON for export purposes
	printL(( json.dumps(json.loads(jsonpickle.encode(obj, make_refs=False)), indent=4)))

def json_print_telnet(obj):
	return json.dumps(json.loads(jsonpickle.encode(obj, make_refs=False)), indent=4)

# tx, address chain search functions

def search_telnet(txcontains, long=1):
	tx_list = []
	hrs_list = []

	#because we allow hrs substitution in txto for transactions, we need to identify where this occurs for searching..

	if txcontains[0] == 'Q':
		for block in m_blockchain:
			for tx in block.transactions:
				if tx.txfrom == txcontains:
					if len(tx.hrs) > 0:
						if state_hrs(tx.hrs) == txcontains:
							hrs_list.append(tx.hrs)

	for tx in transaction_pool:
		if tx.txhash == txcontains or tx.txfrom == txcontains or tx.txto == txcontains or tx.txto in hrs_list:
			#printL(( txcontains, 'found in transaction pool..'
			if long==0: tx_list.append('<tx:txhash> '+tx.txhash+' <transaction_pool>')
			if long==1: tx_list.append(json_print_telnet(tx))

	for block in m_blockchain:
		for tx in block.transactions:
			if tx.txhash == txcontains or tx.txfrom == txcontains or tx.txto == txcontains or tx.txto in hrs_list:
				#printL(( txcontains, 'found in block',str(block.blockheader.blocknumber),'..'
				if long==0: tx_list.append('<tx:txhash> '+tx.txhash+' <block> '+str(block.blockheader.blocknumber))
				if long==1: tx_list.append(json_print_telnet(tx))
	return tx_list

# used for port 80 api - produces JSON output of a specific tx hash, including status of tx, in a block or unconfirmed + timestampe of parent block

def search_txhash(txhash):				#txhash is unique due to nonce.
	for tx in transaction_pool:
		if tx.txhash == txhash:
			printL(( txhash, 'found in transaction pool..'))
			tx_new = copy.deepcopy(tx)
			tx_new.block = 'unconfirmed'
			tx_new.hexsize = len(json_bytestream(tx_new))
			tx_new.status = 'ok'
			return json_print_telnet(tx_new)
	for block in m_blockchain:
		for tx in block.transactions:
			if tx.txhash== txhash:
				tx_new = copy.deepcopy(tx)
				tx_new.block = block.blockheader.blocknumber
				tx_new.timestamp = block.blockheader.timestamp
				tx_new.confirmations = m_blockheight()-block.blockheader.blocknumber
				tx_new.hexsize = len(json_bytestream(tx_new))
				tx_new.amount = tx_new.amount/100000000.000000000
				tx_new.fee = tx_new.fee/100000000.000000000
				printL(( txhash, 'found in block',str(block.blockheader.blocknumber),'..'))
				tx_new.status = 'ok'
				return json_print_telnet(tx_new)
	printL(( txhash, 'does not exist in memory pool or local blockchain..'))
	err = {'status' : 'Error', 'error' : 'txhash not found', 'method' : 'txhash', 'parameter' : txhash}
	return json_print_telnet(err)
	#return False

# used for port 80 api - produces JSON output reporting every transaction for an address, plus final balance..

def search_address(address):
	
	addr = {}
	addr['transactions'] = {}


	if state_address_used(address) != False:
		nonce, balance, pubhash_list = state_get_address(address)
		addr['state'] = {}
		addr['state']['address'] = address
		addr['state']['balance'] = balance/100000000.000000000
		addr['state']['nonce'] = nonce

	for s in stake_list_get():
		if address == s[0]:
			addr['stake'] = {}
			addr['stake']['selector'] = s[2]
		#pubhashes used could be put here..

	for tx in transaction_pool:
		if tx.txto == address or tx.txfrom == address:
			printL(( address, 'found in transaction pool'))
			addr['transactions'][tx.txhash] = {}
			addr['transactions'][tx.txhash]['txhash'] = tx.txhash
			addr['transactions'][tx.txhash]['block'] = 'unconfirmed'
			addr['transactions'][tx.txhash]['amount'] = tx.amount/100000000.000000000
			addr['transactions'][tx.txhash]['fee'] = tx.fee/100000000.000000000
			addr['transactions'][tx.txhash]['nonce'] = tx.nonce
			addr['transactions'][tx.txhash]['ots_key'] = tx.ots_key
			addr['transactions'][tx.txhash]['txto'] = tx.txto
			addr['transactions'][tx.txhash]['txfrom'] = tx.txfrom
			addr['transactions'][tx.txhash]['timestamp'] = 'unconfirmed'


	for block in m_blockchain:
		for tx in block.transactions:
		 if tx.txto == address or tx.txfrom == address:
			printL(( address, 'found in block ', str(block.blockheader.blocknumber), '..' ))
			addr['transactions'][tx.txhash]= {}
			addr['transactions'][tx.txhash]['txhash'] = tx.txhash
			addr['transactions'][tx.txhash]['block'] = block.blockheader.blocknumber
			addr['transactions'][tx.txhash]['timestamp'] = block.blockheader.timestamp
			addr['transactions'][tx.txhash]['amount'] = tx.amount/100000000.000000000
			addr['transactions'][tx.txhash]['fee'] = tx.fee/100000000.000000000
			addr['transactions'][tx.txhash]['nonce'] = tx.nonce
			addr['transactions'][tx.txhash]['ots_key'] = tx.ots_key
			addr['transactions'][tx.txhash]['txto'] = tx.txto
			addr['transactions'][tx.txhash]['txfrom'] = tx.txfrom	

	if len(addr['transactions']) > 0:
		addr['state']['transactions'] = len(addr['transactions'])
	

	if addr == {'transactions': {}}:
		addr = {'status': 'error', 'error' : 'address not found', 'method' : 'address', 'parameter' : address}
	else:
		addr['status'] = 'ok'


	return json_print_telnet(addr)

# return json info on last n tx in the blockchain

def last_tx(n=None):

	addr = {}
	addr['transactions'] = {}

	error = {'status': 'error', 'error' : 'invalid argument', 'method' : 'last_tx', 'parameter' : n}

	if not n:
		n = 1

	try: 	n = int(n)
 	except: return json_print_telnet(error)

 	if n <= 0 or n > 20:
 		return json_print_telnet(error)

 	if len(transaction_pool) != 0:
 		if n-len(transaction_pool) >=0:		# request bigger than tx in pool
 			z = len(transaction_pool)
 			n = n-len(transaction_pool)
 		elif n-len(transaction_pool) <=0:	# request smaller than tx in pool..
 			z = n
 			n = 0
 	
 	 	for tx in reversed(transaction_pool[-z:]):
 	 		addr['transactions'][tx.txhash] = {}
 	 		addr['transactions'][tx.txhash]['txhash'] = tx.txhash
			addr['transactions'][tx.txhash]['block'] = 'unconfirmed'
			addr['transactions'][tx.txhash]['timestamp'] = 'unconfirmed'
			addr['transactions'][tx.txhash]['amount'] = tx.amount/100000000.000000000
			addr['transactions'][tx.txhash]['type'] = tx.type

		if n == 0:
			addr['status'] = 'ok'
			return json_print_telnet(addr)


	for block in reversed(m_blockchain):
			if len(block.transactions) > 0:
				for tx in reversed(block.transactions):
					addr['transactions'][tx.txhash] = {}
 	 				addr['transactions'][tx.txhash]['txhash'] = tx.txhash
					addr['transactions'][tx.txhash]['block'] = block.blockheader.blocknumber
					addr['transactions'][tx.txhash]['timestamp'] = block.blockheader.timestamp
					addr['transactions'][tx.txhash]['amount'] = tx.amount/100000000.000000000
					addr['transactions'][tx.txhash]['type'] = tx.type
					n-=1
					if n == 0:
						addr['status'] = 'ok'
						return json_print_telnet(addr)
	return json_print_telnet(error)

def richlist(n=None):			#only feasible while chain is small..
	if not n:
		n = 5

	error = {'status': 'error', 'error' : 'invalid argument', 'method' : 'richlist', 'parameter' : n}

	try: n=int(n)
	except: return json_print_telnet(error)

	if n<=0 or n > 20:
		return json_print_telnet(error)

	if state_uptodate()==False:
		return json_print_telnet({'status': 'error', 'error': 'leveldb failed', 'method': 'richlist'})

	addr = db.return_all_addresses()
	richlist = sorted(addr, key=itemgetter(1), reverse=True)

	rl = {}
	rl['richlist'] = {}

	if len(richlist) < n:
		n = len(richlist)

	for rich in richlist[:n]:
		rl['richlist'][richlist.index(rich)+1] = {}
		rl['richlist'][richlist.index(rich)+1]['address'] = rich[0]
		rl['richlist'][richlist.index(rich)+1]['balance'] = rich[1]/100000000.000000000

	rl['status'] = 'ok'

	return json_print_telnet(rl)

# return json info on last n blocks

def last_block(n=None):

	if not n:
		n = 1

	error = {'status': 'error', 'error' : 'invalid argument', 'method' : 'last_block', 'parameter' : n}

	try: 	n=int(n)
	except: return json_print_telnet(error)	

	if n <= 0 or n > 20:
		return json_print_telnet(error)

	lb = m_blockchain[-n:]

	last_blocks = {}
	last_blocks['blocks'] = {}

	for block in reversed(lb):

		last_blocks['blocks'][block.blockheader.blocknumber] = {}
		last_blocks['blocks'][block.blockheader.blocknumber]['block_reward'] = block.blockheader.block_reward/100000000.00000000
		last_blocks['blocks'][block.blockheader.blocknumber]['blocknumber'] = block.blockheader.blocknumber
		last_blocks['blocks'][block.blockheader.blocknumber]['blockhash'] = block.blockheader.prev_blockheaderhash
		last_blocks['blocks'][block.blockheader.blocknumber]['number_transactions'] = block.blockheader.number_transactions
		last_blocks['blocks'][block.blockheader.blocknumber]['number_stake'] = block.blockheader.number_stake
		last_blocks['blocks'][block.blockheader.blocknumber]['timestamp'] = block.blockheader.timestamp
		last_blocks['blocks'][block.blockheader.blocknumber]['block_interval'] = block.blockheader.timestamp - m_blockchain[block.blockheader.blocknumber-1].blockheader.timestamp

	last_blocks['status'] = 'ok'

	return json_print_telnet(last_blocks)

# return json info on stake_commit list

def stake_commits(data=None):

	sc = {}
	sc['status'] = 'ok'
	sc['commits'] = {}

	for c in stake_commit:
		#[stake_address, block_number, merkle_hash_tx, commit_hash]
		sc['commits'][str(c[1])+'-'+c[3]] = {}
		sc['commits'][str(c[1])+'-'+c[3]]['stake_address'] = c[0]
		sc['commits'][str(c[1])+'-'+c[3]]['block_number'] = c[1]
		sc['commits'][str(c[1])+'-'+c[3]]['merkle_hash_tx'] = c[2]
		sc['commits'][str(c[1])+'-'+c[3]]['commit_hash'] = c[3]


	return json_print_telnet(sc)

def stakers(data=None):
	#(stake -> address, hash_term, nonce)
	stakers = {}
	stakers['status'] = 'ok'
	stakers['stake_list'] = {}
	for s in stake_list_get():
		stakers['stake_list'][s[0]] = {}
		stakers['stake_list'][s[0]]['address'] = s[0]
		stakers['stake_list'][s[0]]['balance'] = state_balance(s[0])/100000000.00000000
		stakers['stake_list'][s[0]]['hash_terminator'] = s[1]
		stakers['stake_list'][s[0]]['nonce'] = s[2]
		
	return json_print_telnet(stakers)

def next_stakers(data=None):
	#(stake -> address, hash_term, nonce)
	next_stakers = {}
	next_stakers['status'] = 'ok'
	next_stakers['stake_list'] = {}
	for s in next_stake_list_get():
		next_stakers['stake_list'][s[0]] = {}
		next_stakers['stake_list'][s[0]]['address'] = s[0]
		next_stakers['stake_list'][s[0]]['balance'] = state_balance(s[0])/100000000.00000000
		next_stakers['stake_list'][s[0]]['hash_terminator'] = s[1]
		next_stakers['stake_list'][s[0]]['nonce'] = s[2]
		
	return json_print_telnet(next_stakers)

def exp_win(data=None):
	# chain.expected_winner.append([chain.m_blockchain[-1].blockheader.blocknumber+1, winner, winning_staker])
	ew = {}
	ew['status'] = 'ok'
	ew['expected_winner'] = {}
	for e in expected_winner:
		ew['expected_winner'][e[0]] = {}
		ew['expected_winner'][e[0]]['hash'] = e[1]
		ew['expected_winner'][e[0]]['stake_address'] = e[2]
	return json_print_telnet(ew)

def stake_reveal_ones(data=None):

	sr = {}
	sr['status'] = 'ok'
	sr['reveals'] = {}
	# chain.stake_reveal_one.append([stake_address, headerhash, block_number, reveal_one]) #merkle_hash_tx, commit_hash])
	for c in stake_reveal_one:
		sr['reveals'][str(c[1])+'-'+str(c[2])] = {}
		sr['reveals'][str(c[1])+'-'+str(c[2])]['stake_address'] = c[0]
		sr['reveals'][str(c[1])+'-'+str(c[2])]['block_number'] = c[2]
		sr['reveals'][str(c[1])+'-'+str(c[2])]['headerhash'] = c[1]
		sr['reveals'][str(c[1])+'-'+str(c[2])]['reveal'] = c[3]

	return json_print_telnet(sr)

def ip_geotag(data=None):
	
	ip = {}
	ip['status'] = 'ok'
	ip['ip_geotag'] = {}
	ip['ip_geotag'] = ip_list

	x=0
	for i in ip_list:
		ip['ip_geotag'][x] = i
		x+=1

	return json_print_telnet(ip)

def stake_reveals(data=None):

	sr = {}
	sr['status'] = 'ok'
	sr['reveals'] = {}
	#chain.stake_reveal.append([stake_address, block_number, merkle_hash_tx, reveal])
	for c in stake_reveal:
		sr['reveals'][str(c[1])+'-'+c[3]] = {}
		sr['reveals'][str(c[1])+'-'+c[3]]['stake_address'] = c[0]
		sr['reveals'][str(c[1])+'-'+c[3]]['block_number'] = c[1]
		sr['reveals'][str(c[1])+'-'+c[3]]['merkle_hash_tx'] = c[2]
		sr['reveals'][str(c[1])+'-'+c[3]]['reveal'] = c[3]

	return json_print_telnet(sr)

def search(txcontains, long=1):
	for tx in transaction_pool:
		if tx.txhash == txcontains or tx.txfrom == txcontains or tx.txto == txcontains:
			printL(( txcontains, 'found in transaction pool..'))
			if long==1: json_print(tx)
	for block in m_blockchain:
		for tx in block.transactions:
			if tx.txhash== txcontains or tx.txfrom == txcontains or tx.txto == txcontains:
				printL(( txcontains, 'found in block',str(block.blockheader.blocknumber),'..'))
				if long==0: printL(( '<tx:txhash> '+tx.txhash))
				if long==1: json_print(tx)
	return

# chain functions

def f_chain_exist():
	if os.path.isfile('./chain.dat') is True:
		return True
	return False

def f_read_chain():
	block_list = []
	if os.path.isfile('./chain.dat') is False:
		printL(( 'Creating new chain file'))
		block_list.append(creategenesisblock())
		with open("./chain.dat", "a") as myfile:				#add in a new call to create random_otsmss
        		pickle.dump(block_list, myfile)
	try:
			with open('./chain.dat', 'r') as myfile:
				return pickle.load(myfile)
	except:
			printL(( 'IO error'))
			return False

def f_get_last_block():
	return f_read_chain()[-1]

def f_write_chain(block_data):											
		data = f_read_chain()
		for block in block_data:
				data.append(block)
		if block_data is not False:
			printL(( 'Appending data to chain'))
			with open("./chain.dat", "w+") as myfile:				#overwrites wallet..must use w+ as cannot append pickle item
        			pickle.dump(data, myfile)
		return


def f_write_m_blockchain():
	printL(( 'Appending data to chain'))
	with open("./chain.dat", "w+") as myfile:
			pickle.dump(m_blockchain, myfile)
	return

def m_load_chain():
	del m_blockchain[:]
	for block in f_read_chain():
		m_blockchain.append(block)
	return m_blockchain

def m_read_chain():
	if not m_blockchain:
		m_load_chain()
	return m_blockchain

def m_get_block(n):
	if n > len(m_blockchain)-1 or n < 0:
		return False
	return m_read_chain()[n]

def m_get_last_block():
	return m_read_chain()[-1]

def m_create_block(nonce, reveal_list=None):
	return CreateBlock(nonce, reveal_list)

def m_add_block(block_obj, new=1):
	if len(m_blockchain) == 0:
		m_read_chain()

	if validate_block(block_obj, new=new) is True:
		if state_add_block(block_obj) is True:
				m_blockchain.append(block_obj)
				remove_tx_in_block_from_pool(block_obj)
				remove_st_in_block_from_pool(block_obj)
		else: 	
				printL(( 'last block failed state/stake checks, removed from chain'))
				state_validate_tx_pool()
				return False
	else:
		printL(( 'm_add_block failed - block failed validation.'))
		return False
	m_f_sync_chain()
	return True

def m_remove_last_block():
	if not m_blockchain:
		m_read_chain()
	m_blockchain.pop()

def m_blockheight():
	return len(m_read_chain())-1

def height():
	return len(m_blockchain)-1

def m_info_block(n):
	if n > m_blockheight():
		printL(( 'No such block exists yet..'))
		return False
	b = m_get_block(n)
	printL(( 'Block: ', b, str(b.blockheader.blocknumber)))
	printL(( 'Blocksize, ', str(len(json_bytestream(b)))))
	printL(( 'Number of transactions: ', str(len(b.transactions))))
	printL(( 'Validates: ', validate_block(b, last_block = n-1)))

def m_f_sync_chain():

	if m_blockchain[-1].blockheader.blocknumber % 100 == 0:
		f_write_m_blockchain()
	return

def m_verify_chain(verbose=0):
	n = 0
	for block in m_read_chain()[1:]:
		if validate_block(block,last_block=n, verbose=verbose) is False:
				return False
		n+=1
		if verbose is 1:
			sys.stdout.write('.')
			sys.stdout.flush()
	return True


def m_verify_chain_250(verbose=0):		#validate the last 250 blocks or len(m_blockchain)-1..
	n = 0
	if len(m_blockchain) > 250:
		x = 250
	else:
		if len(m_blockchain)==1:
			return True
		x = len(m_blockchain) -1

	for block in m_read_chain()[-x:]:
		if validate_block(block,last_block=block.blockheader.blocknumber-1, verbose=verbose) is False:
				printL(( 'block failed:', block.blockheader.blocknumber))
				return False
		n+=1
		if verbose is 1:
			sys.stdout.write('.')
			sys.stdout.flush()
	return True

#state functions
#first iteration - state data stored in leveldb file
#state holds address balances, the transaction nonce and a list of pubhash keys used for each tx - to prevent key reuse.

def state_load_peers():
	if os.path.isfile('./peers.dat') is True:
		printL(( 'Opening peers.dat'))
		with open('./peers.dat', 'r') as myfile:
			state_put_peers(pickle.load(myfile))
	else:
		printL(( 'Creating peers.dat'))
	 	with open('./peers.dat', 'w+') as myfile:
			pickle.dump(node_list, myfile)
			state_put_peers(node_list)

def state_save_peers():
	with open("./peers.dat", "w+") as myfile:			
        			pickle.dump(state_get_peers(), myfile)

def state_get_peers():
	try: return db.get('node_list')
	except: return False
	
def state_put_peers(peer_list):
	try: db.put('node_list', peer_list)
	except: return False

def stake_list_get():
	try: return db.get('stake_list')
	except: return []

def stake_list_put(sl):
	try: db.put('stake_list', sl)
	except: return False

def next_stake_list_get():
	try: return db.get('next_stake_list')
	except: return []

def next_stake_list_put(next_sl):
	try: db.put('next_stake_list', next_sl)
	except: return False

def state_uptodate():									#check state db marker to current blockheight.
	if m_blockheight() == db.get('blockheight'):
		return True
	return False

def state_blockheight():
	return db.get('blockheight')

def state_get_address(addr):
	try: return db.get(addr)
	except:	return [0,0,[]]

def state_address_used(addr):							#if excepts then address does not exist..
	try: return db.get(addr)
	except: return False 

def state_balance(addr):
	try: return db.get(addr)[1]
	#except:	return False
	except:	return 0 

def state_nonce(addr):
	try: return db.get(addr)[0]
	except: return 0
	#except:	return False

def state_pubhash(addr):
	try: return db.get(addr)[2]
	except: return []
	#except:	return False

def state_hrs(hrs):
	try: return db.get('hrs'+hrs)
	except: return False

def state_validate_tx_pool():
	x=0
	for tx in transaction_pool:
		if tx.state_validate_tx() is False:
			x+=1
			printL(( 'tx', tx.txhash, 'failed..'))
			remove_tx_from_pool(tx)
	if x > 0:
		return False
	return True

# validate and update stake+state for newly appended block.
# can be streamlined to reduce repetition in the added components..
# finish next epoch code..

def state_add_block(block):

	global epoch_PRF
	global epoch_prf
	global hash_chain


	#assert state_blockheight() == m_blockheight()-1, 'state leveldb not @ m_blockheight-1'

	#snapshot of state in case we need to revert to it..

	st1 = []	
	st2 = []
	st3 = state_get_address(block.blockheader.stake_selector)	# to roll back

	st4 = stake_list_get()		# to roll back
	st5 = []

	for st in block.stake:
		st5.append(state_get_address(st.txfrom))	# to roll back

	for tx in block.transactions:
		st1.append(state_get_address(tx.txfrom))
		st2.append(state_get_address(tx.txto))

	y = 0
	
	# first the coinbase address is updated

	db.put(block.blockheader.stake_selector, [st3[0],st3[1]+block.blockheader.block_reward,st3[2]])

	# reminder contents: (state address -> nonce, balance, [pubhash]) (stake -> address, hash_term, nonce)


	if block.blockheader.blocknumber == 1: 	# if block 1: 

		stake_list = []
		sl = []
		next_sl = []

		for st in block.stake:

			if st.txfrom == block.blockheader.stake_selector:			#update txfrom, hash and stake_nonce against genesis for current or next stake_list
				if st.txfrom in m_blockchain[0].stake_list:
					sl.append([st.txfrom, st.hash, 1])
				else:
					printL(( 'designated staker not in genesis..'))
					y=-1000												#triggers block fail..
			else:
				if st.txfrom in m_blockchain[0].stake_list:
					sl.append([st.txfrom, st.hash, 0])
				else:
					next_sl.append([st.txfrom, st.hash, 0])

			z = state_get_address(st.txfrom)
			pub = st.pub
			pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]
			pubhash = sha256(''.join(pub))
			z[2].append(pubhash)
			db.put(st.txfrom, z)	#update the statedb for txfrom's
			
			printL(( 'state st.txfrom', state_get_address(st.txfrom)))

		stake_list = sorted(sl, key=itemgetter(1))
		stake_list_put(stake_list)
		next_stake_list_put(sorted(next_sl, key=itemgetter(1)))
		#numlist(stake_list)

		epoch_PRF = merkle.GEN_range(m_blockchain[block.blockheader.epoch*common.EPOCH_SIZE()].stake_seed, 1, common.EPOCH_SIZE(), 32)
		epoch_prf = pos_block_selector(m_blockchain[block.blockheader.epoch*common.EPOCH_SIZE()].stake_seed, len(stake_list))		#need to add a stake_seed option in block classes
		if stake_list[epoch_prf[block.blockheader.blocknumber-block.blockheader.epoch*common.EPOCH_SIZE()]][0] != block.blockheader.stake_selector:
				printL(( 'stake selector wrong..'))
				y=-1000

		my[0][1].hashchain(epoch=0)
		hash_chain = my[0][1].hc
		wallet.f_save_wallet()

	else:
		
		# how many blocks left in this epoch?

		blocks_left = block.blockheader.blocknumber - (block.blockheader.epoch*common.EPOCH_SIZE())
		blocks_left = common.EPOCH_SIZE()-blocks_left
		#blocks_left = common.EPOCH_SIZE()-block.blockheader.blocknumber-(block.blockheader.epoch*common.EPOCH_SIZE())

		#if block.blockheader.epoch == m_blockchain[-1].blockheader.epoch:	#same epoch..
		stake_list = []
		next_sl = next_stake_list_get()
		sl = stake_list_get()
		u=0
			
		#increase the stake_nonce of state selector..must be in stake list..
		printL(( 'BLOCK:', block.blockheader.blocknumber, 'stake nonce:', block.blockheader.stake_nonce, 'epoch: ', block.blockheader.epoch, 'blocks_left: ', blocks_left-1))

		for s in sl:													
			if block.blockheader.stake_selector == s[0]:
				u=1
				s[2]+=1
				if s[2] != block.blockheader.stake_nonce:
					printL(( 'stake_nonce wrong..'))
					y=-1000
				else:
					stake_list_put(sl)
					stake_list = sl
		if u != 1:
			y=-1000
			printL(( 'stake selector not in stake_list_get'))

		# update and re-order the next_stake_list:

		for st in block.stake:
			pub = st.pub
			pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]
			pubhash = sha256(''.join(pub))
			u=0

			for s in next_sl:
				if st.txfrom == s[0]:		#already in the next stake list, ignore for staker list but update as usual the state_for_address..
					z = state_get_address(st.txfrom)
					z[2].append(pubhash)
					db.put(st.txfrom, z)	#update the statedb for txfrom's
					u=1

			if u==0:
				z = state_get_address(st.txfrom)
				z[2].append(pubhash)
				db.put(st.txfrom, z)
				next_sl.append([st.txfrom, st.hash, 0])

		next_stake_list_put(sorted(next_sl, key=itemgetter(1)))

		# epoch change imminent!

		if blocks_left == 1:		
			printL(( 'EPOCH change: resetting stake_list, activating next_stake_list, updating PRF with seed+entropy, updating wallet hashchains..'))
			del next_sl[:] 
			sl = next_stake_list_get()
			stake_list_put(sl)
			next_stake_list_put(next_sl)

			entropy = m_blockchain[block.blockheader.blocknumber-3].blockheader.headerhash+m_blockchain[block.blockheader.blocknumber-2].blockheader.headerhash+m_blockchain[block.blockheader.blocknumber-1].blockheader.headerhash+block.blockheader.headerhash
			epoch_PRF = merkle.GEN_range(m_blockchain[0].stake_seed+entropy, 1, common.EPOCH_SIZE(), 32)
			my[0][1].hashchain(epoch=block.blockheader.epoch+1)
			hash_chain = my[0][1].hc
			wallet.f_save_wallet()

	# cycle through every tx in the new block to check state
		
	for tx in block.transactions:

		pub = tx.pub
		if tx.type == 'TX':
				pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]

		pubhash = sha256(''.join(pub))

		s1 = state_get_address(tx.txfrom)
		
		# basic tx state checks..

		if s1[1] - tx.amount < 0:
			printL(( tx, tx.txfrom, 'exceeds balance, invalid tx'))
			#return False
			break

		if tx.nonce != s1[0]+1:
			printL(( 'nonce incorrect, invalid tx'))
			printL(( tx, tx.txfrom, tx.nonce))
			#return False
			break

		if pubhash in s1[2]:
			printL(( 'pubkey reuse detected: invalid tx', tx.txhash))
			break

		# add a check to prevent spend from stake address..
		# if tx.txfrom in stake_list_get():
		# printL(( 'attempt to spend from a stake address: invalid tx type'
		# break

		s1[0]+=1
		s1[1] = s1[1]-tx.amount
		s1[2].append(pubhash)
		db.put(tx.txfrom, s1)

		s2 = state_get_address(tx.txto)
		s2[1] = s2[1]+tx.amount
		db.put(tx.txto, s2)

		y+=1

	if y<len(block.transactions):			# if we havent done all the tx in the block we have break, need to revert state back to before the change.
		printL(( 'failed to state check entire block'))
		printL(( 'reverting state'))

		for q in range(len(block.stake)):
			db.put(block.stake[q].txfrom, st5[q])

		for x in range(len(block.transactions)):
			db.put(block.transactions[x].txfrom, st1[x])
			db.put(block.transactions[x].txto, st2[x])
		

		db.put(block.blockheader.stake_selector, st3)		

		return False

	db.put('blockheight', m_blockheight()+1)	#the block is added immediately after this call..
	printL(( block.blockheader.headerhash, str(len(block.transactions)),'tx ',' passed verification.'))
	return True

def verify_chain():
	state_read_genesis()
	if len(m_blockchain) > 1:
		if state_add_block(m_blockchain[1]) == False:
			printL(( 'State verification of block 1 failed'))
			return False
	for x in range(2,len(m_blockchain)):
		if x > len(m_blockchain)-250:
			if validate_block(m_blockchain[x],last_block=m_blockchain[x].blockheader.blocknumber-1, new=0, verbose=1) is False:
				printL(( 'block failed:', m_blockchain[x].blockheader.blocknumber))
				return False
		if state_add_block(m_blockchain[x]) == False:
			return False
	
	return True


def state_read_genesis():
	printL(( 'genesis:'))
	db.zero_all_addresses()
	c = m_get_block(0).state
	for address in c:
		db.put(address[0], address[1])
	return True

def state_read_chain():

	db.zero_all_addresses()
	c = m_get_block(0).state
	for address in c:
		db.put(address[0], address[1])
	
	c = m_read_chain()[2:]
	for block in c:

		# update coinbase address state
		stake_selector = state_get_address(block.blockheader.stake_selector)
		stake_selector[1]+=block.blockheader.block_reward
		db.put(block.blockheader.stake_selector, stake_selector)

		for tx in block.transactions:
			pub = tx.pub
			if tx.type == 'TX':
					pub = [''.join(pub[0][0]),pub[0][1],''.join(pub[2:])]

			pubhash = sha256(''.join(pub))

			s1 = state_get_address(tx.txfrom)

			if s1[1] - tx.amount < 0:
				printL(( tx, tx.txfrom, 'exceeds balance, invalid tx', tx.txhash))
				printL(( block.blockheader.headerhash, 'failed state checks'))
				return False

			if tx.nonce != s1[0]+1:
				printL(( 'nonce incorrect, invalid tx', tx.txhash))
				printL(( block.blockheader.headerhash, 'failed state checks'))
				return False

			if pubhash in s1[2]:
				printL(( 'public key re-use detected, invalid tx', tx.txhash))
				printL(( block.blockheader.headerhash, 'failed state checks'))
				return False

			s1[0]+=1
			s1[1] = s1[1]-tx.amount
			s1[2].append(pubhash)
			db.put(tx.txfrom, s1)							#must be ordered in case tx.txfrom = tx.txto

			s2 = state_get_address(tx.txto)
			s2[1] = s2[1]+tx.amount
			
			db.put(tx.txto, s2)

		printL(( block, str(len(block.transactions)), 'tx ', ' passed'))
	db.put('blockheight', m_blockheight())
	return True

def add_tx_to_pool(tx_class_obj):
	transaction_pool.append(tx_class_obj)
	txhash_timestamp.append(tx_class_obj.txhash)
	txhash_timestamp.append(time())

def add_st_to_pool(st_class_obj):
	stake_pool.append(st_class_obj)

def remove_tx_from_pool(tx_class_obj):
	transaction_pool.remove(tx_class_obj)
	txhash_timestamp.pop(txhash_timestamp.index(tx_class_obj.txhash)+1)
	txhash_timestamp.remove(tx_class_obj.txhash)

def remove_st_from_pool(st_class_obj):
	stake_pool.remove(st_class_obj)
	
def show_tx_pool():
	return transaction_pool

def remove_tx_in_block_from_pool(block_obj):
	for tx in block_obj.transactions:
		for txn in transaction_pool:
			if tx.txhash == txn.txhash:
				remove_tx_from_pool(txn)

def remove_st_in_block_from_pool(block_obj):
	for st in block_obj.stake:
		for stn in stake_pool:
			if st.hash == stn.hash:
				remove_st_from_pool(stn)

def flush_tx_pool():
	del transaction_pool[:]

def flush_st_pool():
	del stake_pool[:]

def validate_tx_in_block(block_obj, new=0):
	x = 0
	for transaction in block_obj.transactions:
		if transaction.validate_tx(new=new) is False:
			printL(( 'invalid tx: ',transaction, 'in block'))
			x+=1
	if x > 0:
		return False
	return True

def validate_st_in_block(block_obj):
	x = 0
	for st in block_obj.stake:
		if st.validate_tx() is False:
			printL(( 'invalid st:', st, 'in block'))
			x+=1
	if x > 0:
		return False
	return True

def validate_tx_pool():									#invalid transactions are auto removed from pool..
	for transaction in transaction_pool:
		if transaction.validate_tx() is False:
			remove_tx_from_pool(transaction)
			printL(( 'invalid tx: ',transaction, 'removed from pool'))

	return True


# block validation

def validate_block(block, last_block='default', verbose=0, new=0):		#check validity of new block..

	if block.blockheader.timestamp == 0:
		printL(( 'Invalid block timestamp ' ))
		return False

	b = block.blockheader

	if b.block_reward != block_reward(b.blocknumber):
		printL(( 'Block reward incorrect for block: failed validation'))
		return False

	if b.epoch != b.blocknumber/common.EPOCH_SIZE():
		printL(( 'Epoch incorrect for block: failed validation'))

	if b.blocknumber == 1:
		x=0
		for st in block.stake:
			if st.txfrom == b.stake_selector:
				x = 1
				if sha256(b.hash) != st.hash:
					printL(( 'Hashchain_link does not hash correctly to terminator: failed validation'))
					return False
		if x != 1:
			printL(( 'Stake selector not in block.stake: failed validation'))
			return False
	else:		# we look in stake_list for the hash terminator and hash to it..
		y=0
		terminator = sha256(b.hash)
		for x in range(b.blocknumber-(b.epoch*common.EPOCH_SIZE())):
			terminator = sha256(terminator)

		for st in stake_list_get():
			if st[0] == b.stake_selector:
				y = 1
				if terminator != st[1]:
					printL(( 'Supplied hash does not iterate to terminator: failed validation'))
					return False
		if y != 1:
				printL(( 'Stake selector not in stake_list for this epoch..'))
				return False
	

	if b.blocknumber > 1:
		if b.hash != cl_hex(epoch_PRF[b.blocknumber-(b.epoch*common.EPOCH_SIZE())],b.reveal_list):
			printL(( "Closest hash not block selector.."))
			return False
		
		if len(b.reveal_list) != len(set(b.reveal_list)):
			printL(( 'Repetition in reveal_list'))
			return False

	 	if new == 1:

			i=0
			for r in b.reveal_list:
				t = sha256(r)
				for x in range(b.blocknumber-(b.epoch*common.EPOCH_SIZE())):
					t = sha256(t)
				for s in stake_list_get():
					if t == s[1]:
						i+=1
		
			if i != len(b.reveal_list):
				printL(( 'Not all the reveal_hashes are valid..'))
				return False


	if sha256(b.stake_selector+str(b.epoch)+str(b.stake_nonce)+str(b.block_reward)+str(b.timestamp)+b.hash+str(b.blocknumber)+b.prev_blockheaderhash+str(b.number_transactions)+b.merkle_root_tx_hash+str(b.number_stake)+b.hashedstake) != b.headerhash:
		printL(( 'Headerhash false for block: failed validation'))
		return False

	if last_block=='default':
		if m_get_last_block().blockheader.headerhash != block.blockheader.prev_blockheaderhash:
			printL(( 'Headerhash not in sequence: failed validation'))
			return False
		if m_get_last_block().blockheader.blocknumber != block.blockheader.blocknumber-1:
			printL(( 'Block numbers out of sequence: failed validation'))
			return False
	else:
		if m_get_block(last_block).blockheader.headerhash != block.blockheader.prev_blockheaderhash:
			printL(( 'Headerhash not in sequence: failed validation'))
			return False
		if m_get_block(last_block).blockheader.blocknumber != block.blockheader.blocknumber-1:
			printL(( 'Block numbers out of sequence: failed validation'))
			return False

	if validate_tx_in_block(block, new=new) == False:
		printL(( 'Block validate_tx_in_block error: failed validation'))
		return False

	if validate_st_in_block(block) == False:
		printL(( 'Block validate_st_in_block error: failed validation'))
		return False

	if len(block.transactions) == 0:
		txhashes = sha256('')
	else:
		txhashes = []
		for transaction in block.transactions:
			txhashes.append(transaction.txhash)

	if merkle_tx_hash(txhashes) != block.blockheader.merkle_root_tx_hash:
		printL(( 'Block hashedtransactions error: failed validation'))
		return False

	sthashes = []
	for st in block.stake:
		sthashes.append(st.hash)

	if sha256(''.join(sthashes)) != b.hashedstake:
		printL(( 'Block hashedstake error: failed validation'))

	if verbose==1:
		#pass
		printL(( b.blocknumber, 'True'))

	return True


# simple transaction creation and wallet functions using the wallet file..

def wlt():
	return merkle.numlist(wallet.list_addresses())

def create_my_tx(txfrom, txto, amount, fee=0):
	if isinstance(txto, int):
		txto = my[txto][0]

	tx = SimpleTransaction().create_simple_transaction(txfrom=my[txfrom][0], txto=txto, amount=amount, data=my[txfrom][1], fee=fee)

	if tx and tx.state_validate_tx():
		add_tx_to_pool(tx)
		wallet.f_save_winfo()	#need to keep state after tx ..use wallet.info to store index..far faster than loading the 55mb wallet..
		return tx
	
	return False


