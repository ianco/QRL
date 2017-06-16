# QRL testnet node..
# -features POS, quantum secure signature scheme..

__author__ = 'pete'
import time, struct, random, copy, decimal
import chain, wallet, merkle

import ntp
import logger
from twisted.internet.protocol import ServerFactory, Protocol 
from twisted.internet import reactor, defer, task, threads
from merkle import sha256, numlist, hexseed_to_seed, mnemonic_to_seed, GEN_range, random_key
from operator import itemgetter
from collections import Counter, defaultdict
from math import ceil
from blessings import Terminal
import statistics
import json
import sys
import common
import fork

log, consensus = logger.getLogger(__name__)

TELNET_PORT = 2000
API_PORT = 8080
PEER_PORT = 9000

cmd_list = ['balance', 'mining', 'seed', 'hexseed', 'recoverfromhexseed', 'recoverfromwords', 'stakenextepoch', 'stake', 'address', 'wallet', 'send', 'mempool', 'getnewaddress', 'quit', 'exit', 'search' ,'json_search', 'help', 'savenewaddress', 'listaddresses','getinfo','blockheight', 'json_block', 'reboot', 'peers']
api_list = ['block_data','stats', 'ip_geotag','exp_win','txhash', 'address', 'empty', 'last_tx', 'stake_reveal_ones', 'last_block', 'richlist', 'ping', 'stake_commits', 'stake_reveals', 'stake_list', 'stakers', 'next_stakers', 'latency']

term = Terminal();
print term.enter_fullscreen

common.initialize_parameters(sys.argv)
print 'running network ', common.ROOT_NODE(), ' ', common.EPOCH_SIZE()
sys.exit()

#State Class
class state:
	def __init__(self):
		self.current = 'unsynced'
		self.epoch_diff = -1

	def update(self, state):
		self.current = state
		if self.current == 'synced':
			self.epoch_diff = 0
			global last_pos_cycle
			last_pos_cycle = time.time()
		elif self.current == 'unsynced':
			global last_bk_time
			last_bk_time = time.time()
			schedule_peers_blockheight()
		elif self.current == 'forked':
			stop_post_block_logic()

	def update_epoch_diff(self, value):
		self.epoch_diff = value

	def __repr__(self):
		return self.current

chain.state = state()

#Initializing function to log console output
printL = logger.PrintHelper(log, chain.state).printL
consensusL = logger.PrintHelper(consensus, chain.state).printL
chain.printL = printL
wallet.printL = printL
merkle.printL = printL
fork.printL = printL
ntp.printL = printL

r1_time_diff = defaultdict(list) #r1_time_diff[block_number] = { 'stake_address':{ 'r1_time_diff': value_in_ms }}
r2_time_diff = defaultdict(list) #r2_time_diff[block_number] = { 'stake_address':{ 'r2_time_diff': value_in_ms }}

pending_blocks = {}	#Used only for synchronization of blocks
last_pos_cycle = 0
last_selected_height = 0
last_bk_time = 0
last_pb_time = 0
next_header_hash = None
next_block_number = None

def log_traceback(exctype, value, tb):				#Function to log error's traceback
	printL (( '*** Error ***' ))
	printL (( str(exctype) ))
	printL (( str(value) ))
	#printL (( tb ))

sys.excepthook = log_traceback

def parse(data):
		return data.replace('\r\n','')

def stop_monitor_bk():
	try: reactor.monitor_bk.cancel()
	except: pass

def restart_monitor_bk():
	stop_monitor_bk()
	reactor.monitor_bk = reactor.callLater(60, monitor_bk)

def monitor_bk():
	global last_pos_cycle, last_bk_time, last_pb_time
	
	if (chain.state.current == 'synced' or chain.state.current == 'unsynced') and time.time() - last_pos_cycle > 90:
		if chain.state.current == 'synced':
			stop_post_block_logic()
			reset_everything()
			chain.state.update('unsynced')
			chain.state.update_epoch_diff(-1)
		elif time.time() - last_bk_time > 120:
			last_pos_cycle = time.time()
			printL (( ' POS cycle activated by monitor_bk() ' ))
			restart_post_block_logic()
			chain.state.update('synced')
			chain.state.update_epoch_diff(0)


	if chain.state.current == 'syncing' and time.time() - last_pb_time > 60:
		stop_post_block_logic()
		reset_everything()
		chain.state.update('unsynced')
		chain.state.update_epoch_diff(-1)
	reactor.monitor_bk = reactor.callLater(60, monitor_bk)

def peers_blockheight_headerhash():
	for peer in f.peers:
		peer.fetch_headerhash_n(chain.m_blockheight())

def check_fork_status():
	current_height = chain.m_blockheight()
	block_hash_counter = Counter()
	for peer in f.peers:
		if current_height in peer.blocknumber_headerhash.keys():
			block_hash_counter[peer.blocknumber_headerhash[current_height]] += 1

	blockhash = block_hash_counter.most_common(1)
	if blockhash:
		blockhash = blockhash[0][0]
		actual_blockhash = chain.m_get_block(current_height).blockheader.headerhash
		if  actual_blockhash != blockhash:
			printL (( 'Blockhash didnt matched in peers_blockheight()' ))
			printL (( 'Local blockhash - ', actual_blockhash ))
			printL (( 'Consensus blockhash - ', blockhash ))
			fork.fork_recovery(current_height, chain, randomize_headerhash_fetch)
			return True
	return 
		
def peers_blockheight():
	if chain.state.current=='syncing':
		return
	if check_fork_status():
		return
	
	block_height_counter = Counter()
	
	for peer in f.peers:
		block_height_counter[peer.blockheight] += 1
	
	blocknumber = block_height_counter.most_common(1)
	if not blocknumber:
		return			#TODO : Re-Schedule with delay
	
	blocknumber = blocknumber[0][0]
	
	if blocknumber > chain.height(): #chain.m_blockheight():  len(chain.m_blockchain)
		pending_blocks['target'] = blocknumber + 1
		printL (( 'Calling downloader from peers_blockheight due to no POS CYCLE ', blocknumber ))
		printL (( 'Download block from ', chain.height()+1 ,' to ', blocknumber ))
		global last_pb_time
		last_pb_time = time.time()
		chain.state.update('syncing')
		randomize_block_fetch(chain.height() + 1)
	return
	
def schedule_peers_blockheight(delay=100):
	try: reactor.peers_blockheight.cancel()
	except Exception: pass
	reactor.peers_blockheight = reactor.callLater(delay, peers_blockheight)
	try: reactor.peers_blockheight_headerhash.cancel()
	except Exception: pass
	reactor.peers_blockheight_headerhash = reactor.callLater(70, peers_blockheight_headerhash)

# pos functions. an asynchronous loop. 

# first block 1 is created with the stake list for epoch 0 decided from circulated st transactions

def pre_pos_1(data=None):		# triggered after genesis for block 1..
	printL(( 'pre_pos_1'))
	# are we a staker in the stake list?

	if chain.mining_address in chain.m_blockchain[0].stake_list:
		printL(('mining address:', chain.mining_address,' in the genesis.stake_list'))
		
		chain.my[0][1].hashchain(epoch=0)
		chain.hash_chain = chain.my[0][1].hc

		printL(('hashchain terminator: ', chain.hash_chain[-1]))
		st = chain.StakeTransaction().create_stake_transaction(chain.hash_chain[-1])
		wallet.f_save_winfo()
		chain.add_st_to_pool(st)
		f.send_st_to_peers(st)			#send the stake tx to generate hashchain terminators for the staker addresses..
		printL(( 'await delayed call to build staker list from genesis'))
		reactor.callLater(5, pre_pos_2, st)
		return

	printL(( 'not in stake list..no further pre_pos_x calls'))
	return

def pre_pos_2(data=None):	
	printL(( 'pre_pos_2'))

	# assign hash terminators to addresses and generate a temporary stake list ordered by st.hash..

	tmp_list = []

	for st in chain.stake_pool:
		if st.txfrom in chain.m_blockchain[0].stake_list:
			tmp_list.append([st.txfrom, st.hash, 0])
	
	chain.stake_list = sorted(tmp_list, key=itemgetter(1))

	numlist(chain.stake_list)

	printL(( 'genesis stakers ready = ', len(chain.stake_list),'/',len(chain.m_blockchain[0].stake_list)))
	printL(( 'node address:', chain.mining_address))

	if len(chain.stake_list) < chain.minimum_required_stakers:		# stake pool still not full..reloop..
		f.send_st_to_peers(data)
		printL(( 'waiting for stakers.. retry in 5s'))
		reactor.callID = reactor.callLater(5, pre_pos_2, data)
		return

	for s in chain.stake_list:
		if s[0] == chain.mining_address:
			spos = chain.stake_list.index(s)
	
	chain.epoch_prf = chain.pos_block_selector(chain.m_blockchain[-1].stake_seed, len(chain.stake_pool))	 #Use PRF to decide first block selector..
	#def GEN_range(SEED, start_i, end_i, l=32): 
	chain.epoch_PRF = GEN_range(chain.m_blockchain[-1].stake_seed, 1, common.EPOCH_SIZE(), 32)

	printL(( 'epoch_prf:', chain.epoch_prf[1]))
	printL(( 'spos:', spos))

	if spos == chain.epoch_prf[1]:
		printL(( 'designated to create block 1: building block..'))

		# create the genesis block 2 here..

		b = chain.m_create_block(chain.hash_chain[-2])
		#chain.json_printL(((b)
		#printL(( chain.validate_block(b)))
		if chain.m_add_block(b) == True:
			f.send_block_to_peers(b)
			#f.get_m_blockheight_from_peers()
			printL(( '**POS commit call later 30 (genesis)...**'))
			f.send_stake_reveal_one()
			reactor.callLater(15, reveal_two_logic)
									
	else:
		printL(( 'await block creation by stake validator:', chain.stake_list[chain.epoch_prf[1]][0]))
		#f.send_st_to_peers(data)
	return



	
def process_transactions(num):
	tmp_num = num
	for tx in chain.pending_tx_pool:
		tmp_num -= 1
		tx_peer = tx[1]
		tx = tx[0]
		if tx.validate_tx() != True:
			printL(( '>>>TX ', tx.txhash, 'failed validate_tx'))
			continue

		if tx.state_validate_tx() != True:
			printL(( '>>>TX', tx.txhash, 'failed state_validate'))
			continue

		printL(( '>>>TX - ', tx.txhash, ' from - ', tx_peer.transport.getPeer().host, ' relaying..'))
		chain.add_tx_to_pool(tx)

		txn_msg = tx_peer.wrap_message('TX',tx.transaction_to_json())
		for peer in tx_peer.factory.peers:
			if peer != tx_peer:
				peer.transport.write(txn_msg)
	
	for i in range(num-tmp_num):
		del chain.pending_tx_pool[0]
		del chain.pending_tx_pool_hash[0]

# we end up here exactly 30 seconds after the last block arrived or was created and sent out..
# collate the reveal_ones messages to decide the winning hash..send out reveal_two's with our vote..


def reveal_two_logic(data=None):
	printL(( 'reveal_two_logic'))

	if len(chain.pending_tx_pool)>0 and len(chain.transaction_pool)<10:
		printL (( 'Processing TXNs if any' ))
		process_transactions(5)

	reveals = []
	curr_time = int(time.time()*1000)
	global r1_time_diff
	r1_time_diff[chain.m_blockchain[-1].blockheader.blocknumber+1] = map(lambda t1: curr_time - t1,r1_time_diff[chain.m_blockchain[-1].blockheader.blocknumber+1])
		
	for s in chain.stake_reveal_one:
		if s[1] == chain.m_blockchain[-1].blockheader.headerhash and s[2] == chain.m_blockchain[-1].blockheader.blocknumber+1:
			reveals.append(s[3])

	# are we forked and creating only our own blocks?

	if len(reveals) <= 1:
			printL(( 'only received one reveal for this block..quitting reveal_two_logic'))
			f.get_m_blockheight_from_peers()
			f.send_last_stake_reveal_one()
			reactor.callIDR15 = reactor.callLater(5, reveal_two_logic)
			#restart_post_block_logic()
			return

	# what is the PRF output and expected winner for this block?	


	epoch = (chain.m_blockchain[-1].blockheader.blocknumber+1)/common.EPOCH_SIZE()			#+1 = next block
	winner = chain.cl_hex(chain.epoch_PRF[(chain.m_blockchain[-1].blockheader.blocknumber+1)-(epoch*common.EPOCH_SIZE())], reveals)

	if f.stake == True:
		if chain.mining_address in [s[0] for s in chain.stake_list_get()]:
				f.send_stake_reveal_two(winner)

	if chain.mining_address in [s[0] for s in chain.stake_reveal_one]:
		for t in chain.stake_reveal_one:
			print t[0], chain.mining_address
			if t[0]==chain.mining_address:
				if t[2]==chain.m_blockchain[-1].blockheader.blocknumber+1:
					our_reveal = t[3]
					reactor.callIDR2 = reactor.callLater(15, reveal_three_logic, winner=winner, reveals=reveals, our_reveal=our_reveal)
					return
	
	reactor.callIDR2 = reactor.callLater(15, reveal_three_logic, winner=winner, reveals=reveals)
	return


# here ~30s after last block..
# collate the R2 messages to see if we are creating the block by network consensus..

def reveal_three_logic(winner, reveals, our_reveal=None):
	printL(( 'reveal_three_logic:'))

	if len(chain.pending_tx_pool)>0 and len(chain.transaction_pool)<10:
		printL (( 'Processing TXNs if any' ))
		process_transactions(5)
	
	if (len(chain.stake_reveal_two)<=1):
		f.send_last_stake_reveal_two()
		reactor.callIDR2 = reactor.callLater(5, reveal_three_logic, winner=winner, reveals=reveals, our_reveal=our_reveal)
		
	# rank the received votes for winning reveal_one hashes
	if not pos_d(chain.m_blockchain[-1].blockheader.blocknumber+1, chain.m_blockchain[-1].blockheader.headerhash):
		printL (( "POS_d failed to make consensus at R2 " ))
		restart_post_block_logic()
		return

	printL(( 'R2 CONSENSUS:', chain.pos_d[1],'/', chain.pos_d[2],'(', chain.pos_d[3],'%)', 'voted/staked emission %:', chain.pos_d[6],'v/s ', chain.pos_d[4]/100000000.0, '/', chain.pos_d[5]/100000000.0  ,'for: ', chain.pos_d[0] ))

	if f.stake == True:
		if chain.mining_address in [s[0] for s in chain.stake_list_get()]:
			f.send_stake_reveal_three(chain.pos_d[0])

	reactor.callIDR3 = reactor.callLater(15, reveal_four_logic, reveals, our_reveal)

	return
	
def reveal_four_logic(reveals, our_reveal):
	printL(('reveal_four_logic: '))

	if pos_consensus(chain.m_blockchain[-1].blockheader.blocknumber+1, chain.m_blockchain[-1].blockheader.headerhash) == False:
		#failure recovery entry here..
		reset_everything()
		printL(('pos_consensus() is false: failure recovery mode..'))
		restart_post_block_logic()
		return

	consensusL(( 'R3 CONSENSUS:', chain.pos_consensus[1],'/', chain.pos_consensus[2],'(', chain.pos_consensus[3],'%)', 'voted/staked emission %:', chain.pos_consensus[6],'v/s ', chain.pos_consensus[4]/100000000.0, '/', chain.pos_consensus[5]/100000000.0  ,'for: ', chain.pos_consensus[0], 'stake_address: ', chain.pos_consensus[7], 'block_number: ', chain.m_blockchain[-1].blockheader.blocknumber+1 ))

	if consensus_rules_met() == False:
		reset_everything()
		restart_post_block_logic()
		return

	chain.pos_flag = [chain.m_blockchain[-1].blockheader.blocknumber+1, chain.m_blockchain[-1].blockheader.headerhash]		#set POS flag for block logic sync..

	global last_pos_cycle
	last_pos_cycle = time.time()

	if our_reveal == chain.pos_consensus[0]:
		printL(( 'CHOSEN BLOCK SELECTOR'))
		f.sync = 1
		f.partial_sync = [0, 0]
		reactor.callLater(10, create_new_block, our_reveal, reveals)
		return

	printL(( 'CONSENSUS winner: ', chain.pos_consensus[7], 'hash ', chain.pos_consensus[0]))
	printL(( 'our_reveal', our_reveal))

	#reactor.ban_staker = reactor.callLater(25, ban_staker, chain.pos_consensus[7])

	return


def ban_staker(stake_address):
	del chain.stake_reveal_one[:]					# as we have just created this there can be other messages yet for next block, safe to erase
	del chain.stake_reveal_two[:]
	del chain.stake_reveal_three[:]
	chain.ban_stake(stake_address)
	return


# consensus rules..

def consensus_rules_met():

	if chain.pos_consensus[3] >= 75:
		if chain.pos_consensus[6] >= 75:
			return True

	printL(( 'Network consensus inadequate..rejected'))
	return False


# create new block..

def create_new_block(winner, reveals):
		printL(( 'create_new_block'))
		tx_list = []
		for t in chain.transaction_pool:
			tx_list.append(t.txhash)
		block_obj = chain.create_stake_block(tx_list, winner, reveals)

		if chain.m_add_block(block_obj) is True:				
			stop_all_loops()
			del chain.stake_reveal_one[:]					# as we have just created this there can be other messages yet for next block, safe to erase
			del chain.stake_reveal_two[:]
			del chain.stake_reveal_three[:]
			f.send_block_to_peers(block_obj)				# relay the block
		else:
			printL(( 'bad block'))
			return
	
	# if staking
		restart_post_block_logic()
		return


def pos_missed_block(data=None):
	printL(( '** Missed block logic ** - trigger m_blockheight recheck..'))
	reset_everything()
	f.get_m_blockheight_from_peers()
	f.send_m_blockheight_to_peers()
	#restart_post_block_logic()
	return

def reset_everything(data=None):
	printL(( '** resetting loops and emptying chain.stake_reveal_one, reveal_two, chain.pos_d and chain.expected_winner '))
	stop_all_loops()
	del chain.stake_reveal_one[:]
	del chain.stake_reveal_two[:]
	del chain.stake_reveal_three[:]
	del chain.expected_winner[:]
	del chain.pos_d[:]
	del chain.pos_consensus[:]
	del chain.pos_flag[:]
	return


def stop_all_loops(data=None):
	printL(( '** stopping timing loops **'))
	try:	reactor.ban_staker.cancel()
	except: pass
	try:	reactor.callIDR15.cancel()	#reveal loop
	except:	pass
	try:	reactor.callID.cancel()		#cancel the ST genesis loop if still running..
	except: pass
	try: 	reactor.callIDR3.cancel()
	except:	pass 
	try: 	reactor.callIDR2.cancel()
	except: pass
	try: 	reactor.callID2.cancel()		#cancel the soon to be re-called missed block logic..
	except: pass
	return

def stop_pos_loops(data=None):
	printL(( '** stopping pos loops and resetting flags **'))
	try:	reactor.callIDR15.cancel()	#reveal loop
	except:	pass
	try: 	reactor.callIDR3.cancel()
	except: pass
	try: 	reactor.callIDR2.cancel()
	except: pass
	try:	reactor.callID.cancel()		#cancel the ST genesis loop if still running..
	except: pass

	# flags
	del chain.pos_flag[:]
	del chain.pos_d[:]
	del chain.pos_consensus[:]
	return

def start_all_loops(data=None):
	printL(( '** starting loops **'))
	#reactor.callID2 = reactor.callLater(120, pos_missed_block)
	reactor.callIDR15 = reactor.callLater(15, reveal_two_logic)
	return

# remove old messages - this is only called when we have just added the last block so we know that messages related to this block and older are no longer necessary..

	#chain.stake_reveal_two.append([z['stake_address'],z['headerhash'], z['block_number'], z['reveal_one'], z['nonce'], z['winning_hash']])		
	#chain.stake_reveal_one.append([z['stake_address'],z['headerhash'], z['block_number'], z['reveal_one'], z['reveal_two'], rkey])
	#chain.stake_reveal_three.append([z['stake_address'],z['headerhash'], z['block_number'], z['consensus_hash'], z['nonce2']])

def filter_reveal_one_two(blocknumber = None):
	if not blocknumber:
		blocknumber = chain.m_blockchain[-1].blockheader.blocknumber

	chain.stake_reveal_one = filter(lambda s: s[2] > blocknumber, chain.stake_reveal_one)
	
	chain.stake_reveal_two = filter(lambda s: s[2] > blocknumber, chain.stake_reveal_two)

	chain.stake_reveal_three = filter(lambda s: s[2] > blocknumber, chain.stake_reveal_three)

	return

def select_blockheight_by_consensus():
	global last_selected_height
	block_height_counter = Counter()
	for s in chain.stake_reveal_three:
		if s[2] > last_selected_height:
			block_height_counter[s[2]] += 1
	target_block_height = block_height_counter.most_common(1)

	if len(target_block_height) == 0:
		return None

	last_selected_height = target_block_height[0][0]
	return last_selected_height


# supra factory block logic 

# pre block logic..

def pre_block_logic(block_obj):
	if block_obj.blockheader.blocknumber <= chain.height():
		return
	if chain.validate_block_timestamp(block_obj.blockheader.timestamp, block_obj.blockheader.blocknumber):
		printL (( 'Block rejected due to NTP' ))
		return 

	global next_header_hash, next_block_number, last_pos_cycle, sync_tme, last_bk_time, last_selected_height, last_pb_time, pending_blocks
	bk_time_diff = time.time() - last_bk_time
	last_bk_time = time.time()
	blocknumber = block_obj.blockheader.blocknumber
	headerhash = block_obj.blockheader.headerhash
	time_diff = time.time() - last_pos_cycle

	if chain.state.current == 'unsynced':
		schedule_peers_blockheight()

	try:
		if chain.state.current == 'unsynced':
			if blocknumber > chain.m_blockheight() + 1:
				blocknumber = select_blockheight_by_consensus()
				if blocknumber != block_obj.blockheader.blocknumber:
					printL (( 'Block number mismatch with consensus | Rejected - ', blocknumber ))
					return
				target_block_number = next_block_number
				target_header_hash = next_header_hash
				next_block_number = blocknumber + 1
				next_header_hash = headerhash
				if target_block_number == None:
					printL (( 'Got 1 block, need 1 more  ', blocknumber ))
					peers_blockheight_headerhash()
					return
				chain.state.update_epoch_diff((blocknumber/common.EPOCH_SIZE()) - (chain.m_blockheight()/common.EPOCH_SIZE()))
				if chain.state.epoch_diff == 0:
					if not (pos_consensus(target_block_number, target_header_hash)):
						printL (( 'Not matched with reveal, skipping block number ', blocknumber ))
						return

					if not consensus_rules_met():
						printL (( ' Consensus ', chain.pos_consensus[3] ,'% below 75% for block number ', blocknumber ))
						return

					printL (( 'Unsynced on Same Epoch' ))
				else:
					if not(blocknumber == target_block_number and block_obj.blockheader.prev_blockheaderhash == target_header_hash):
						printL (( 'Mismatch pre_block_logic' ))
						printL (( 'Found Blocknumber ', blocknumber, ' Expected blocknumber ', target_block_number))
						printL (( 'Found prev_headerhash ', target_header_hash, ' Expected prev_headerhash ', block_obj.blockheader.prev_blockheaderhash))
						next_block_number = None
						next_header_hash = None
						return
					printL (( 'Unsynced on Different Epoch' ))
				if check_fork_status():
					return
				pending_blocks = {}
				pending_blocks[blocknumber] = [None, block_obj]
				pending_blocks['target'] = blocknumber
				printL (( 'Calling downloader from pre_block_logic due to block number ', blocknumber ))
				printL (( 'Download block from ', chain.height()+1 ,' to ', blocknumber-1 ))
				last_pb_time = time.time()
				chain.state.update('syncing')
				randomize_block_fetch(chain.m_blockheight() + 1)

			elif blocknumber == chain.height() + 1:
				if blocknumber>1 and not pos_consensus(chain.m_blockchain[-1].blockheader.blocknumber+1, chain.m_blockchain[-1].blockheader.headerhash):
					printL (( 'POS consensus failed for blocknumber ', blocknumber ))
					return
				
				chain.recent_blocks.append(block_obj)
				synchronising_update_chain()
				if bk_time_diff > 20:
					chain.state.update('synced')
					restart_post_block_logic()


		elif chain.state.current == 'syncing':
			if blocknumber == next_block_number and block_obj.blockheader.prev_blockheaderhash == next_header_hash:
				if not (pos_consensus(next_block_number, next_header_hash)):
					printL (( 'Not matched with reveal, skipping block number ', blocknumber ))
					return

				if not consensus_rules_met():
					printL (( ' Consensus ', chain.pos_consensus[3] ,'% below 75% for block number ', blocknumber ))
					return


				pending_blocks[blocknumber] = [None, block_obj]
				next_block_number += 1
				next_header_hash = headerhash

		elif chain.state.current == 'synced':
			if  time.time() - last_pos_cycle > 120 and (not pos_consensus(next_block_number, next_header_hash)):
				printL (( 'Not matched with reveal, skipping block number ', blocknumber ))
				return

			if not received_block_logic(block_obj):
				printL (( 'next_header_hash and next_block_number didnt match for ', blocknumber ))
				printL (( 'Expected next_header_hash ', chain.m_blockchain[-1].blockheader.headerhash, ' received ', block_obj.blockheader.prev_blockheaderhash ))
				printL (( 'Expected next_block_number ', chain.m_blockchain[-1].blockheader.blocknumber+1, ' received ', blocknumber ))
	except Exception as Ex:
		printL (( ' Exception in received_block_logic for block number ', blocknumber ))
		printL (( str(Ex) ))

	return

def received_block_logic(block_obj):

	# rapid logic

	if block_obj.blockheader.headerhash == chain.m_blockchain[-1].blockheader.headerhash:
			return

	if block_obj.blockheader.blocknumber != chain.m_blockheight()+1:
			printL(( '>>>BLOCK - out of order - need', str(chain.m_blockheight()+1), ' received ', str(block_obj.blockheader.blocknumber), block_obj.blockheader.headerhash))#, ' from ', self.transport.getPeer().host
			f.get_m_blockheight_from_peers()
			return
	
	if block_obj.blockheader.prev_blockheaderhash != chain.m_blockchain[-1].blockheader.headerhash:
			# Fork recovery should not be called from here. As it could be a fake block 
			# or block from some other POS cycle.
			# in case if the block is from the current cycle, it will be switch to unsynced
			# after some delay
			printL(( '>>>WARNING: FORK..'))
			printL(( 'Block rejected hash doesnt matches with prev_blockheaderhash' ))
			printL(( 'Expected prev_headerhash - ', chain.m_blockchain[-1].blockheader.headerhash ))
			printL(( 'Found prev_headerhash - ', block_obj.blockheader.prev_blockheaderhash ))
			#fork.fork_recovery(block_obj.blockheader.blocknumber-1, chain, randomize_headerhash_fetch)
			return

	# pos checks
	if block_obj.blockheader.blocknumber > 1:
		if block_meets_consensus(block_obj.blockheader) != True:
			return

	# validation and state checks, then housekeeping

	if chain.m_add_block(block_obj) is True:				
		f.send_block_to_peers(block_obj)
		
		restart_post_block_logic()
		return True

	return

def stop_post_block_logic(delay = 0):
	try: reactor.post_block_logic.cancel()
	except Exception: pass

def restart_post_block_logic(delay = 0):
	stop_post_block_logic()
	reactor.post_block_logic = reactor.callLater(delay, post_block_logic)

# post block logic we initiate the next POS cycle, send R1, send ST, reset POS flags and remove unnecessary messages in chain.stake_reveal_one and _two..

def post_block_logic():

	stop_all_loops()
	start_all_loops()

	filter_reveal_one_two()

	del chain.pos_flag[:]
	del chain.pos_d[:]
	del chain.expected_winner[:]

	if f.stake == True:
		if chain.mining_address in [s[0] for s in chain.stake_list_get()]:
				f.send_stake_reveal_one()
		if chain.mining_address not in [s[0] for s in chain.next_stake_list_get()]:
				f.send_st_to_peers(chain.StakeTransaction().create_stake_transaction())
				wallet.f_save_winfo()


	return


# network consensus rules set here for acceptable stake validator counts and weight based upon address balance..
# to be updated..

def block_meets_consensus(blockheader_obj):

	if chain.m_blockchain[-1].blockheader.blocknumber+1!=blockheader_obj.blocknumber or chain.m_blockchain[-1].blockheader.headerhash!=blockheader_obj.prev_blockheaderhash:
		printL(( 'POS reveal_three_logic not activated for this block..'))
		return False

	# check consensus rules..stake validators have to be in 75% agreement or if less then 75% of funds have to be agreement..

	if consensus_rules_met() is False:
		return False
	
	# is it the correct winner?

	if blockheader_obj.hash != chain.pos_consensus[0]:
		printL(( 'Winning hash does not match consensus..rejected'))
		return False

	if blockheader_obj.stake_selector != chain.pos_consensus[7]:
		printL(( 'Stake selector does not match consensus..rejected'))
		return False

	return True


# synchronisation functions.. use random sampling of connected nodes to reduce chatter between nodes..


def get_synchronising_blocks(block_number):
	f.sync = 0
	f.requested[1] += 1
	stop_all_loops()
	
	behind = block_number-chain.m_blockheight()
	peers = len(f.peers)

	if f.requested[0] == chain.m_blockheight()+1:
		if f.requested[1] <= len(f.peers):
			return

	printL(( 'local node behind connection by ', behind, 'blocks - synchronising..'))
	f.requested = [chain.m_blockheight()+1, 0]
	f.get_block_n_random_peer(chain.m_blockheight()+1)
	return

def update_target_peers(block_number):
	f.target_peers = {}
	printL (( str(f.peers) ))
	for peer in f.peers:
		if peer.identity not in f.fork_target_peers:
			continue
		printL (( peer.identity, peer.identity in f.peers_blockheight, f.peers_blockheight.keys() ))
		if peer.identity in f.peers_blockheight:
			printL (( peer.identity, f.peers_blockheight[peer.identity], '>=', block_number-1 ))
			if f.peers_blockheight[peer.identity] >= block_number - 1:
				f.target_peers[peer.identity] = peer
				f.target_retry[peer.identity] = 0

def randomize_block_fetch(block_number):
	if chain.state.current!='syncing':
		return
	if block_number<=chain.height():
		printL (( 'Already in blockchain... skipping' ))
		return

	global pending_blocks
	if block_number in pending_blocks:
		host_port = pending_blocks[block_number][0]
		f.target_retry[host_port] += 1
		if f.target_retry[host_port] == 2 and host_port in f.target_peers:
			printL (( 'Removing : ', host_port, ' from target_peers' ))
			del f.target_peers[host_port]

	if block_number not in pending_blocks or pending_blocks[block_number][1]<=10:
		block_monitor = reactor.callLater(15, randomize_block_fetch, block_number)
		if len(f.peers) > 0:
			try:
				if block_number % common.EPOCH_SIZE() == 0 or len(f.target_peers) == 0:
					f.get_m_blockheight_from_peers()
					update_target_peers(min(block_number+common.EPOCH_SIZE(),pending_blocks['target']))
				if len(f.target_peers) > 0:
					random_peer = f.target_peers[random.choice(f.target_peers.keys())]
					if block_number in pending_blocks:
						pending_blocks[block_number][0] = random_peer.identity
						pending_blocks[block_number][1] += 1
						pending_blocks[block_number][2] = None
						pending_blocks[block_number][3] = block_monitor
					else:
						pending_blocks[block_number] = [random_peer.identity, 0, None, block_monitor]
					random_peer.fetch_block_n(block_number)
				else:
					printL (('Target peers 0, block_number: ', block_number ))
			except KeyError as ex:
				printL(( 'Exception at randomize_block_fetch' ))
				printL(( str(ex) ))
		else:
			printL (( 'No peers connected.. Will try again... randomize_block_fetch: ', block_number ))
	else:
		if pending_blocks[block_number][1] > 10: #forked if retried more than 10 times
			pending_blocks = {}
			fork.fork_recovery(block_number-1, chain, randomize_headerhash_fetch)
			return


def randomize_headerhash_fetch(block_number):
	if chain.state.current != 'forked':
		return
	if block_number not in fork.pending_blocks or fork.pending_blocks[block_number][1]<=10: #retry only 11 times
		headerhash_monitor = reactor.callLater(15, randomize_headerhash_fetch, block_number)
		if len(f.peers) > 0:
			try:
				if len(f.fork_target_peers) == 0:
					for peer in f.peers:
						f.fork_target_peers[peer.identity] = peer
				if len(f.fork_target_peers) > 0:
					random_peer = f.fork_target_peers[random.choice(f.fork_target_peers.keys())]
					count = 0
					if block_number in fork.pending_blocks:
						count = fork.pending_blocks[block_number][1]+1
					fork.pending_blocks[block_number] = [random_peer.identity, count, None, headerhash_monitor]
					random_peer.fetch_headerhash_n(block_number)
			except:
				printL (( 'Exception at randomize_headerhash_fetch' ))
		else:
			printL (( 'No peers connected.. Will try again... randomize_headerhash_fetch: ', block_number ))
	else:
		chain.state.update('unsynced')


def synchronising_update_chain(data=None):
	printL(( 'sync update chain'))
	
	chain.recent_blocks.sort(key=lambda x: x.blockheader.blocknumber)			# sort the contents of the recent_blocks pool in ascending block number order..
	tmp_recent_blocks = []
	for b in chain.recent_blocks:
		if b.blockheader.blocknumber != chain.m_blockheight()+1:
			printL(( 'Received Block ', b.blockheader.blocknumber , ' expected block number ', chain.m_blockheight()+1 ))
			pass
		else:
			if b.blockheader.prev_blockheaderhash != chain.m_blockchain[-1].blockheader.headerhash:
				printL(( 'potential fork..block hashes do not fit, discarded'))
				fork.fork_recovery(b.blockheader.blocknumber-1, chain, randomize_headerhash_fetch)
				continue	#forked blocks?
			else:
				chain.m_add_block(b, new=0)
		if b.blockheader.blocknumber <= chain.m_blockheight():
			continue
		tmp_recent_blocks.append(b)
	
	chain.recent_blocks = tmp_recent_blocks
	del chain.recent_blocks[:]
	f.get_m_blockheight_from_random_peer()
	return


# blockheight map for connected nodes - when the blockheight seems up to date after a sync or error, we check all connected nodes to ensure all on same chain/height..
# note - may not return correctly during a block propagation..
# once working alter to identify fork better..

def blockheight_map():

	#i = [block_number, headerhash, self.transport.getPeer().host]

	printL(( 'blockheight_map:'))
	printL(( chain.blockheight_map))

	# first strip out any laggards..
	chain.blockheight_map = filter(lambda s: s[0]>=chain.m_blockheight(), chain.blockheight_map)

	bmap_fail = 0

	# next identify any node entries which are not exactly correct..

	for s in chain.blockheight_map:
		if s[0]==chain.m_blockheight() and s[1]==chain.m_blockchain[-1].blockheader.headerhash:
			printL(( 'node: ', s[2], '@', s[0], 'w/:', s[1], 'OK'))
		elif s[0] > chain.m_blockheight():
			printL(( 'warning..', s[2], 'at blockheight', s[0]))
			bmap_fail = 1

	# wipe it..

	del chain.blockheight_map[:]

	if bmap_fail == 1:
		return False

	return True


# rank the winning hashes for the current block number, by number, by address balance and both..after receipt of each valid R2 msg


def pos_d(block_number, headerhash):
	p = []
	l = []
	curr_time = int(time.time()*1000)
	global r2_time_diff
	r2_time_diff[chain.m_blockchain[-1].blockheader.blocknumber+1] = map(lambda t2: curr_time - t2, r2_time_diff[chain.m_blockchain[-1].blockheader.blocknumber+1])

	for s in chain.stake_reveal_two:
		if s[1]==headerhash and s[2]==block_number:
			p.append(chain.state_balance(s[0]))
			l.append([chain.state_balance(s[0]),s[5]])

	if len(p) <= 1:
		printL (( 'POS_D failed headerhash or block_number didnt match' ))
		printL (( 'Expected headerhash : ',headerhash ))
		printL (( 'Expected block_number : ',block_number ))
		return False

	total_staked = sum(p)
	total_voters = len(l)
	
	c = Counter([s[1] for s in l]).most_common(2)		#list containing tuple count of (winning hash, count) - first two..
	
	# all votes same..should be this every time
	if len(c) != 1 :
		printL(( 'warning, more than one winning hash is being circulated by incoming R2 messages..'))

	stake_address = None

	for s in chain.stake_reveal_one:
		if s[3]==c[0][0]:
			stake_address = s[0]

	if not stake_address:
		printL(( 'POS_D failed as no reveal_one message was in stake_address' ))
		return False

	percentage_a = decimal.Decimal(c[0][1])/decimal.Decimal(total_voters)*100			#percentage of voters choosing winning hash

	total_voted=0
	for s in l:
		if s[1]==c[0][0]:
			total_voted+=s[0]

	percentage_d = decimal.Decimal(total_voted)/decimal.Decimal(total_staked)*100	

	chain.pos_d = [c[0][0], c[0][1], total_voters, percentage_a, total_voted, total_staked, percentage_d, stake_address]

	return True

# rank the consensus hashes..

def pos_consensus(block_number, headerhash):

	#chain.stake_reveal_three.append([stake_address,headerhash, block_number, consensus_hash, nonce2])

	p = []
	l = []

	for s in chain.stake_reveal_three:
		if s[1]==headerhash and s[2]==block_number:
			p.append(chain.state_balance(s[0]))
			l.append([chain.state_balance(s[0]), s[3], s[5]])

	if len(p) <= 1:
		return False

	total_staked = sum(p)
	total_voters = len(l)
	
	c = Counter([s[1] for s in l]).most_common(2)		#list containing tuple count of (winning hash, count) - first two..
	
	# all votes same..should be this every time

	if len(c) != 1 :
		printL(( 'warning, more than one consensus_hash is being circulated by incoming R3 messages..'))

	stake_address = None
	for s in chain.stake_reveal_one:
		if s[3]==c[0][0]:
			stake_address = s[0]

	if not stake_address:
		return False

	percentage_a = decimal.Decimal(c[0][1])/decimal.Decimal(total_voters)*100			#percentage of voters choosing winning hash

	del f.fork_target_peers
	f.fork_target_peers = {}
	total_voted=0
	for s in l:
		if s[1]==c[0][0]:
			total_voted+=s[0]
			if s[2]:
				f.fork_target_peers[s[2].identity] = s[2]

	percentage_d = decimal.Decimal(total_voted)/decimal.Decimal(total_staked)*100

	chain.pos_consensus = [c[0][0], c[0][1], total_voters, percentage_a, total_voted, total_staked, percentage_d, stake_address]
	#return [c[0][0], c[0][1], total_voters, percentage_a, total_voted, total_staked, percentage_d, stake_address]
	return True


# factories and protocols..

class ApiProtocol(Protocol):

	def __init__(self):
		pass

	def parse_cmd(self, data):

		data = data.split()			#typical request will be: "GET /api/{command}/{parameter} HTTP/1.1"
		
		#printL(( data
		
		if len(data) == 0: return

		if data[0] != 'GET' and data[0] != 'OPTIONS':
			return False

		if data[0] == 'OPTIONS':
			http_header_OPTIONS = ("HTTP/1.1 200 OK\r\n"
								   "Access-Control-Allow-Origin: *\r\n"
								   "Access-Control-Allow-Methods: GET\r\n"
								   "Access-Control-Allow-Headers: x-prototype-version,x-requested-with\r\n"
								   "Content-Length: 0\r\n"
								   "Access-Control-Max-Age: 2520\r\n"
								   "\r\n")
			self.transport.write(http_header_OPTIONS)
			return 

		data = data[1][1:].split('/')

		if data[0].lower() != 'api':
			return False

		if len(data) == 1:
			data.append('')

		if data[1] == '':
			data[1] = 'empty'

		if data[1].lower() not in api_list:			#supported {command} in api_list
			error = {'status': 'error', 'error': 'supported method not supplied', 'parameter' : data[1] }
			self.transport.write(chain.json_print_telnet(error))
			return False
		
		my_cls = ApiProtocol()					#call the command from api_list directly
		api_call = getattr(my_cls, data[1].lower())	
		
		if len(data) < 3:
			json_txt = api_call()
			#self.transport.write(api_call())
		else:
			json_txt = api_call(data[2])
			#self.transport.write(api_call(data[2]))

		http_header_GET = ("HTTP/1.1 200 OK\r\n"
						   "Content-Type: application/json\r\n"
						   "Content-Length: %s\r\n"
						   "Access-Control-Allow-Headers: x-prototype-version,x-requested-with\r\n"
						   "Access-Control-Max-Age: 2520\r\n"
						   "Access-Control-Allow-Origin: *\r\n"
						   "Access-Control-Allow-Methods: GET\r\n"
						   "\r\n") % (str(len(json_txt)))

		self.transport.write(http_header_GET+json_txt)
		return

	def exp_win(self, data=None):
		printL(( '<<< API expected winner call'))
		return chain.exp_win(data)

	def ping(self, data=None):
		printL(( '<<< API network latency ping call'))
		f.ping_peers()									 # triggers ping for all connected peers at timestamp now. after pong response list is collated. previous list is delivered.
		pings = {}
		pings['status'] = 'ok'
		pings['peers'] = {}
		pings['peers'] = chain.ping_list
		return chain.json_print_telnet(pings)

	def stakers(self, data=None):
		printL(( '<<< API stakers call'))
		return chain.stakers(data)

	def next_stakers(self, data=None):
		printL(( '<<< API next_stakers call'))
		return chain.next_stakers(data)

	def stake_commits(self, data=None):
		printL(( '<<< API stake_commits call'))
		return chain.stake_commits(data)

	def stake_reveals(self, data=None):
		printL(( '<<< API stake_reveals call'))
		return chain.stake_reveals(data)

	def stake_reveal_ones(self, data=None):
		printL(( '<<< API stake_reveal_ones'))
		return chain.stake_reveal_ones(data)

	def richlist(self, data=None):
		printL(( '<<< API richlist call'))
		return chain.richlist(data)

	def last_block(self, data=None):
		printL(( '<<< API last_block call'))
		return chain.last_block(data)

	def last_tx(self, data=None):
		printL(( '<<< API last_tx call'))
		return chain.last_tx(data)

	def ip_geotag(self, data=None):
		printL(( '<<< API ip_geotag call'))
		f.ip_geotag_peers()
		return chain.ip_geotag(data)

	def empty(self, data=None):
		error = {'status': 'error','error' : 'no method supplied', 'methods available' : 'block_data, stats, txhash, address, last_tx, last_block, richlist, ping, stake_commits, stake_reveals, stakers, next_stakers'}
		return chain.json_print_telnet(error)

	def block_data(self, data=None):				# if no data = last block ([-1])			#change this to add error.. 
		error = {'status': 'error', 'error' : 'block not found', 'method': 'block_data', 'parameter' : data}
		printL(( '<<< API block data call', data	))
		if not data:
			#return chain.json_printL((_telnet(chain.m_get_last_block())
			data = chain.m_get_last_block()
			data1 = copy.deepcopy(data)
			data1.status = 'ok'
			return chain.json_print_telnet(data1)
		try: int(data)														# is the data actually a number?
		except: 
			return chain.json_print_telnet(error)
		#js_bk = chain.json_printL((_telnet(chain.m_get_block(int(data)))
		js_bk = chain.m_get_block(int(data))
		#if js_bk == 'false':
		if js_bk == False:
			return chain.json_print_telnet(error)
		else:
			js_bk1 = copy.deepcopy(js_bk)
			js_bk1.status = 'ok'
			js_bk1.blockheader.block_reward = js_bk1.blockheader.block_reward/100000000.000000000
			return chain.json_print_telnet(js_bk1)

	def stats(self, data=None):
		printL(( '<<< API stats call'))

		# calculate staked/emission %
		b=0
		for s in chain.stake_list_get():
			b+=chain.state_balance(s[0])
		staked = decimal.Decimal((b/100000000.000000000)/(chain.db.total_coin_supply()/100000000.000000000)*100).quantize(decimal.Decimal('1.00')) #/100000000.000000000)
		staked = float(str(staked))
		# calculate average blocktime over last 100 blocks..

		z=0
		t = []

		for b in reversed(chain.m_blockchain[-100:]):
			if b.blockheader.blocknumber > 0:
				x = b.blockheader.timestamp-chain.m_blockchain[b.blockheader.blocknumber-1].blockheader.timestamp
				t.append(x)
				z+=x

		#printL(( 'mean', z/len(chain.m_blockchain[-100:]), 'max', max(t), 'min', min(t), 'variance', max(t)-min(t)

		net_stats = {'status': 'ok', 'version': chain.version_number, 'block_reward' : chain.m_blockchain[-1].blockheader.block_reward/100000000.00000000, 'stake_validators' : len(chain.m_blockchain[-1].blockheader.reveal_list), 'epoch' : chain.m_blockchain[-1].blockheader.epoch, 'staked_percentage_emission' : staked , 'network' : 'qrl testnet', 'network_uptime': time.time()-chain.m_blockchain[1].blockheader.timestamp,'block_time' : z/len(chain.m_blockchain[-100:]), 'block_time_variance' : max(t)-min(t) ,'blockheight' : chain.m_blockheight(), 'nodes' : len(f.peers)+1, 'emission': chain.db.total_coin_supply()/100000000.000000000, 'unmined' : 21000000-chain.db.total_coin_supply()/100000000.000000000 }
		return chain.json_print_telnet(net_stats)

	def txhash(self, data=None):
		printL(( '<<< API tx/hash call', data))
		return chain.search_txhash(data)

	def address(self, data=None):
		printL(( '<<< API address call', data))
		return chain.search_address(data)

	def dataReceived(self, data=None):
		self.parse_cmd(data)
		self.transport.loseConnection()
	
	def connectionMade(self):
		self.factory.connections += 1
		#printL(( '>>> new API connection'

	def connectionLost(self, reason):
		#printL(( '<<< API disconnected'
		self.factory.connections -= 1

	def latency(self, type=None):
		output = {}
		if type and type.lower() in ['mean', 'median', 'last']:
			for block_num in chain.stake_validator_latency.keys():
				output[block_num] = {}
				for stake in chain.stake_validator_latency[block_num].keys():
					time_list = chain.stake_validator_latency[block_num][stake]
					print time_list
					output[block_num][stake] = {}
					if type.lower()=='mean':
						output[block_num][stake]['r1_time_diff'] =  statistics.mean(time_list['r1_time_diff'])
						if 'r2_time_diff' in time_list:
							output[block_num][stake]['r2_time_diff'] =  statistics.mean(time_list['r2_time_diff'])
					elif type.lower()=='last':
						output[block_num][stake]['r1_time_diff'] = time_list['r1_time_diff'][-1]
						if 'r2_time_diff' in time_list:
							output[block_num][stake]['r2_time_diff'] = time_list['r2_time_diff'][-1]
					elif type.lower()=='median':
						output[block_num][stake]['r1_time_diff'] = statistics.median(time_list['r1_time_diff'])
						if 'r2_time_diff' in time_list:
							output[block_num][stake]['r2_time_diff'] = statistics.median(time_list['r2_time_diff'])
		else:
			output = chain.stake_validator_latency
		output = json.dumps(output)
		return output

class WalletProtocol(Protocol):

	def __init__(self):		
		pass

	def parse_cmd(self, data):
	
		data = data.split()
		args = data[1:]

		if len(data) != 0:
		 if data[0] in cmd_list:			

			if data[0] == 'getnewaddress':
				self.getnewaddress(args)
				return

			if data[0] == 'hexseed':
				for c in chain.my:
					if type(c[1])== list:
						pass
					else:
						if c[1].type == 'XMSS':
							self.transport.write('Address: '+ c[1].address+'\r\n')
							self.transport.write('Recovery seed: '+c[1].hexSEED+'\r\n')
				return

			if data[0] == 'seed':
				for c in chain.my:
					if type(c[1])== list:
						pass
					else:
						if c[1].type == 'XMSS':
							self.transport.write('Address: '+ c[1].address+'\r\n')
							self.transport.write('Recovery seed: '+c[1].mnemonic+'\r\n')
				return

			elif data[0] == 'search':
				if not args:
					self.transport.write('>>> Usage: search <txhash or Q-address>'+'\r\n')
					return
				for result in chain.search_telnet(args[0], long=0):
					self.transport.write(result+'\r\n')
				return

			elif data[0] == 'json_search':
				if not args:
					self.transport.write('>>>Usage: search <txhash or Q-address>'+'\r\n')
					return
				for result in chain.search_telnet(args[0], long=1):
					self.transport.write(result+'\r\n')
				return

			elif data[0] == 'json_block':
				
				if not args:
					#chain.json_printL(((chain.m_get_last_block())
					self.transport.write(chain.json_print_telnet(chain.m_get_last_block())+'\r\n')
					return
				try: int(args[0])
				except:	
						self.transport.write('>>> Try "json_block <block number>" '+'\r\n') 
						return

				if int(args[0]) > chain.m_blockheight():
					self.transport.write('>>> Block > Blockheight'+'\r\n')
					return

				self.transport.write(chain.json_print_telnet(chain.m_get_block(int(args[0])))+'\r\n')
				return

			elif data[0] == 'savenewaddress':
				self.savenewaddress()
			
			elif data[0] == 'recoverfromhexseed':
				if not args or not hexseed_to_seed(args[0]):
					self.transport.write('>>> Usage: recoverfromhexseed <paste in hexseed>'+'\r\n')
					self.transport.write('>>> Could take up to a minute..'+'\r\n')
					self.transport.write('>>> savenewaddress if Qaddress matches expectations..'+'\r\n')
					return

				self.transport.write('>>> trying.. this could take up to a minute..'+'\r\n')
				addr = wallet.getnewaddress(type='XMSS', SEED=hexseed_to_seed(args[0]))
				self.factory.newaddress = addr
				self.transport.write('>>> Recovery address: '+ addr[1].address +'\r\n')
				self.transport.write('>>> Recovery seed phrase: '+addr[1].mnemonic + '\r\n')
				self.transport.write('>>> hexSEED confirm: '+addr[1].hexSEED+'\r\n')
				self.transport.write('>>> savenewaddress if Qaddress matches expectations..'+'\r\n')
				return

			elif data[0] == 'recoverfromwords':
				if not args:
					self.transport.write('>>> Usage: recoverfromwords <paste in 32 mnemonic words>'+'\r\n')
					return
				self.transport.write('>>> trying..this could take up to a minute..'+'\r\n')
				if len(args) != 32:
					self.transport.write('>>> Usage: recoverfromwords <paste in 32 mnemonic words>'+'\r\n')
					return
				args = ' '.join(args)
				addr = wallet.getnewaddress(type='XMSS', SEED=mnemonic_to_seed(args))
				self.factory.newaddress = addr
				self.transport.write('>>> Recovery address: '+ addr[1].address +'\r\n')
				self.transport.write('>>> Recovery hexSEED: '+addr[1].hexSEED + '\r\n')
				self.transport.write('>>> Mnemonic confirm: '+addr[1].mnemonic+'\r\n')
				self.transport.write('>>> savenewaddress if Qaddress matches expectations..'+'\r\n')
				return

			elif data[0] == 'stake':
				self.transport.write('>> Toggling stake from: '+str(f.stake)+' to: '+str(not f.stake)+'\r\n')
				f.stake = not f.stake
				printL(( 'STAKING set to: ', f.stake))
				return

			elif data[0] == 'stakenextepoch':
				self.transport.write('>>> Sending a stake transaction for address: '+chain.mining_address+' to activate next epoch('+str(common.EPOCH_SIZE()-(chain.m_blockchain[-1].blockheader.blocknumber-(chain.m_blockchain[-1].blockheader.epoch*common.EPOCH_SIZE())))+' blocks time)'+'\r\n')
				printL(( 'STAKE for address:', chain.mining_address))
				f.send_st_to_peers(chain.StakeTransaction().create_stake_transaction())
				return
			
			elif data[0] == 'send':
				self.send_tx(args)

			elif data[0] == 'mempool':
				self.transport.write('>>> Number of transactions in memory pool: '+ str(len(chain.transaction_pool))+'\r\n')

			elif data[0] == 'help':
				self.transport.write('>>> QRL ledger help: try quit, wallet, send, getnewaddress, search, recoverfromhexseed, recoverfromwords, stake, stakenextepoch, mempool, json_block, json_search, seed, hexseed, getinfo, peers, or blockheight'+'\r\n')
				#removed 'hrs, hrs_check,'
			elif data[0] == 'quit' or data[0] == 'exit':
				self.transport.loseConnection()

			#elif data[0] == 'balance':
			#	self.state_balance(args)

			elif data[0] == 'listaddresses':
					addresses, num_sigs, types = wallet.inspect_wallet()
					
					for x in range(len(addresses)):
						self.transport.write(str(x)+', '+addresses[x]+'\r\n')

			elif data[0] == 'wallet':
					self.wallet()
					
			elif data[0] == 'getinfo':
					self.transport.write('>>> Version: '+chain.version_number+'\r\n')
					self.transport.write('>>> Uptime: '+str(time.time()-start_time)+'\r\n')
					self.transport.write('>>> Nodes connected: '+str(len(f.peers))+'\r\n')
					self.transport.write('>>> Staking set to: '+ str(f.stake)+'\r\n')
					self.transport.write('>>> Sync status: '+chain.state.current+'\r\n')

			elif data[0] == 'blockheight':
					self.transport.write('>>> Blockheight: '+str(chain.m_blockheight())+'\r\n')

			elif data[0] == 'peers':
					self.transport.write('>>> Connected Peers:\r\n')
					for peer in f.peers:
						self.transport.write('>>> ' + peer.identity + " [" + peer.version + "]  blockheight: " + str(peer.blockheight) + '\r\n')

			elif data[0] == 'reboot':
				if len(args)<1:
					self.transport.write('>>> reboot <password>\r\n')
					self.transport.write('>>> or\r\n')
					self.transport.write('>>> reboot <password> <nonce>\r\n')
					return 
				json_hash, err = None, None
				if len(args)==2:
					json_hash, status = chain.generate_reboot_hash(args[0], args[1])
				else:
					json_hash, status = chain.generate_reboot_hash(args[0])
				if json_hash:
					f.send_reboot(json_hash)
					chain.state.update('synced')
					restart_post_block_logic()
				self.transport.write(status)
		else:
			return False

		return True

	def dataReceived(self, data):
		self.factory.recn += 1
		if self.parse_cmd(parse(data)) == False:
			self.transport.write(">>> Command not recognised. Use 'help' for details"+'\r\n')
	
	def connectionMade(self):
		self.transport.write(self.factory.stuff)
		self.factory.connections += 1
		if self.factory.connections > 1:
			printL(( 'only one local connection allowed'))
			self.transport.write('only one local connection allowed, sorry')
			self.transport.loseConnection()
		else:
			if self.transport.getPeer().host == '127.0.0.1':
				printL(( '>>> new local connection', str(self.factory.connections), self.transport.getPeer()))
				# welcome functions to run here..
			else:
				self.transport.loseConnection()
				printL(( 'Unauthorised remote login attempt..'))

	def connectionLost(self, reason):
		self.factory.connections -= 1

	# local wallet access functions..

	def getbalance(self, addr):
		if chain.state_uptodate() is False:
			self.transport.write('>>> LevelDB not up to date..'+'\r\n')
			return
		if not addr: 
			self.transport.write('>>> Usage: getbalance <address> (Addresses begin with Q)'+'\r\n')
			return
		if addr[0][0] != 'Q':
			self.transport.write('>>> Usage: getbalance <address> (Addresses begin with Q)'+'\r\n')
			return
		if chain.state_address_used(addr[0]) is False:
			self.transport.write('>>> Unused address.'+'\r\n')
			return
		self.transport.write('>>> balance:  '+str(chain.state_balance(addr[0]))+'\r\n')
		return

	def getnewaddress(self, args):
		if not args or len(args) > 2:
			self.transport.write('>>> Usage: getnewaddress <n> <type (XMSS, WOTS or LDOTS)>'+'\r\n')
			self.transport.write('>>> i.e. getnewaddress 4096 XMSS'+'\r\n')
			self.transport.write('>>> or: getnewaddress 128 LDOTS'+'\r\n')
			self.transport.write('>>> (new address creation can take a while, please be patient..)'+'\r\n')
			return 
		else:
			try:	int(args[0])
			except:
					self.transport.write('>>> Invalid number of signatures. Usage: getnewaddress <n signatures> <type (XMSS, WOTS or LDOTS)>'+'\r\n')
					self.transport.write('>>> i.e. getnewaddress 4096 XMSS'+'\r\n')
					return

		#SHORTEN WITH args[1].upper() 

		if args[1] != 'XMSS' and args[1] != 'xmss' and args[1] != 'WOTS' and args[1] != 'wots' and args[1] != 'LDOTS' and args[1] != 'ldots' and args[1] != 'LD':
			self.transport.write('>>> Invalid signature address type. Usage: getnewaddress <n> <type (XMSS, WOTS or LDOTS)>'+'\r\n')
			self.transport.write('>>> i.e. getnewaddress 4096 XMSS'+'\r\n')
			return

		if args[1] == 'xmss':
			args[1] = 'XMSS'

		if args[1] == 'wots':
			args[1] = 'WOTS'

		if args[1] == 'ldots' or args[1] == 'LD':
			args[1] = 'LDOTS'

		if int(args[0]) > 256 and args[1] != 'XMSS':
			self.transport.write('>>> Try a lower number of signatures or you may be waiting a very long time...'+'\r\n')
			return

		self.transport.write('>>> Creating address..please wait'+'\r\n')
		addr = wallet.getnewaddress(int(args[0]), args[1])

		if type(addr[1]) == list:
			self.transport.write('>>> Keypair type: '+''.join(addr[1][0].type+'\r\n'))
			self.transport.write('>>> Signatures possible with address: '+str(len(addr[1]))+'\r\n')
			self.transport.write('>>> Address: '+''.join(addr[0])+'\r\n')

		else:	#xmss
			self.transport.write('>>> Keypair type: '+''.join(addr[1].type+'\r\n'))
			self.transport.write('>>> Signatures possible with address: '+str(addr[1].signatures)+'\r\n')
			self.transport.write('>>> Address: '+addr[1].address+'\r\n')

		self.transport.write(">>> type 'savenewaddress' to append to wallet file"+'\r\n')
		self.factory.newaddress = addr
		return

	def savenewaddress(self):
		if not self.factory.newaddress:
			self.transport.write(">>> No new addresses created, yet. Try 'getnewaddress'"+'\r\n')
			return
		wallet.f_append_wallet(self.factory.newaddress)
		self.transport.write('>>> new address saved in wallet.'+'\r\n')
		return

	def send_tx(self, args):
		if not args or len(args) < 3:
			self.transport.write('>>> Usage: send <from> <to> <amount>'+'\r\n')
			self.transport.write('>>> i.e. send 0 4 100'+'\r\n')
			self.transport.write('>>> ^ will send 100 coins from address 0 to 4 from the wallet'+'\r\n')
			self.transport.write('>>> <to> can be a pasted address (starts with Q)'+'\r\n')
			return

		try: int(args[0])
		except: 
				self.transport.write('>>> Invalid sending address. Try a valid number from your wallet - type wallet for details.'+'\r\n')
				return
		
		if int(args[0]) > len(wallet.list_addresses())-1:
				self.transport.write('>>> Invalid sending address. Try a valid number from your wallet - type wallet for details.'+'\r\n')
				return

		if len(args[1]) > 1 and args[1][0] != 'Q' and chain.state_hrs(args[1]) != False:
			pass
		elif args[1][0] == 'Q':
			pass
		else:
			try: int(args[1])
			except:
					self.transport.write('>>> Invalid receiving address - addresses must start with Q. Try a number from your wallet.'+'\r\n')
					return
			if int(args[1]) > len(wallet.list_addresses())-1:
					self.transport.write('>>> Invalid receiving address - addresses must start with Q. Try a number from your wallet.'+'\r\n')
					return	
			args[1] = int(args[1])
		
		balance = chain.state_balance(chain.my[int(args[0])][0])

		try: float(args[2])
		except: 
				self.transport.write('>>> Invalid amount type. Type a number (less than or equal to the balance of the sending address)'+'\r\n')
				return



		#to_send = decimal.Decimal(format(decimal.Decimal(args[2]), '.8f')*100000000)
		amount = decimal.Decimal(decimal.Decimal(args[2])*100000000).quantize(decimal.Decimal('1'), rounding= decimal.ROUND_HALF_UP)


		if balance < amount:
				self.transport.write('>>> Invalid amount to send. Type a number less than or equal to the balance of the sending address'+'\r\n')
				return

		tx = chain.create_my_tx(txfrom=int(args[0]), txto=args[1], amount=amount)
		
		#self.transport.write(msg+'\r\n')
		if tx is False:
			return
		
		#printL(( 'new local tx: ', tx
		if tx.validate_tx():
			if not tx.state_validate_tx():
				self.transport.write('>>> OTS key reused')
				return
		else:
			self.transport.write('>>> TXN failed at validate_tx')
			printL(( '>>> TXN failed at validate_tx' ))
			return

		f.send_tx_to_peers(tx)
		self.transport.write('>>> '+str(tx.txhash))
		self.transport.write('>>> From: '+str(tx.txfrom)+' To: '+str(tx.txto)+' For: '+str(tx.amount/100000000.000000000)+'\r\n'+'>>>created and sent into p2p network'+'\r\n')
		return

	def wallet(self):
		if chain.state_uptodate() == False:
			chain.state_read_chain()
		self.transport.write('>>> Wallet contents:'+'\r\n')
		y=0
		for address in wallet.list_addresses():
			self.transport.write(str(y)+str(address)+'\r\n')
			y+=1

class p2pProtocol(Protocol):

	def __init__(self):		
		self.buffer = ''
		self.messages = []
		self.identity = None
		self.blockheight = None
		self.version = ''
		self.blocknumber_headerhash = {}
		pass

	def parse_msg(self, data):
		try:
			jdata = json.loads(data)
		except:
			return

		func = jdata['type']
		try:
			if 'data' in jdata:
				getattr(self, func)(jdata['data'])
			else:
				getattr(self, func)()
		except:
			printL (( "parse_msg Exception while calling " ))
			printL (( "Func name ", func ))
			#printL (( "JSON data ", jdata ))
			pass

	def reboot(self, data):
		hash_dict = json.loads(data)
		if not ('hash' in hash_dict and 'nonce' in hash_dict):
			return
		if not chain.validate_reboot(hash_dict['hash'], hash_dict['nonce']):
			return
		for peer in self.factory.peers:
			if peer!=self:
				peer.transport.write(self.wrap_message('reboot',data))
		printL (( 'Initiating Reboot Sequence.....' ))
		
		chain.state.update('synced')
		restart_post_block_logic()

	def TX(self, data):				#tx received..
		self.recv_tx(data)
		return
		
	def ST(self, data):
		try: st = chain.StakeTransaction().json_to_transaction(data)
		except: 
			printL(( 'st rejected - unable to decode serialised data - closing connection'))
			self.transport.loseConnection()
			return

		for t in chain.stake_pool:			#duplicate tx already received, would mess up nonce..
			if st.hash == t.hash:
				return
			
		if st.validate_tx() and st.state_validate_tx():
			chain.add_st_to_pool(st)
		else:
			printL(( '>>>ST',st.hash, 'invalid state validation failed..')) #' invalid - closing connection to ', self.transport.getPeer().host
			return

		printL(( '>>>ST - ', st.hash, ' from - ', self.transport.getPeer().host, ' relaying..'))
			
		for peer in self.factory.peers:
			if peer != self:
				peer.transport.write(self.wrap_message('ST',st.transaction_to_json()))
		return


	def BM(self, data=None):	# blockheight map for synchronisation and error correction prior to POS cycle resync..
		if not data:
			printL(( '<<<Sending block_map', self.transport.getPeer().host))
			z = {}
			z['block_number'] = chain.m_blockchain[-1].blockheader.blocknumber
			z['headerhash'] = chain.m_blockchain[-1].blockheader.headerhash
			self.transport.write(self.wrap_message('BM',chain.json_encode(z)))
			return
		else:
			printL(( '>>>Receiving block_map'))
			z = chain.json_decode(data)
			block_number = z['block_number']
			headerhash = z['headerhash'].encode('latin1')

			i = [block_number, headerhash, self.transport.getPeer().host]
			printL(( i))
			if i not in chain.blockheight_map:
				chain.blockheight_map.append(i)
			return	

	def BK(self, data):			#block received
		try:	block = chain.json_decode_block(data)
		except:
			printL(( 'block rejected - unable to decode serialised data', self.transport.getPeer().host))
			return
		pre_block_logic(block)
		return

	def PB(self, data):
		global pending_blocks, last_bk_time, last_pb_time
		last_pb_time = time.time()
		thisPeerHost = self.transport.getHost()
		try:
			block = chain.json_decode_block(data)
			blocknumber = block.blockheader.blocknumber
			printL (( '>>>Received Block #', block.blockheader.blocknumber))
			if blocknumber in pending_blocks and self.identity == pending_blocks[blocknumber][0]:
				printL (( 'Found in Pending List' ))
				if not chain.m_add_block(block):
					printL (( "Failed to add block by m_add_block, re-requesting the block #",blocknumber ))
					return

				try: pending_blocks[blocknumber][3].cancel()
				except Exception: pass
				del pending_blocks[blocknumber]
				if blocknumber+1 < pending_blocks['target']:
					randomize_block_fetch(blocknumber+1)
				else:
					for i in range(chain.m_blockheight()+1, chain.m_blockheight()+1+len(pending_blocks)-1): # -1 as 'target' key is stored into pending_blocks
						block = pending_blocks[i][1]				
						del pending_blocks[i]
						if not chain.m_add_block(block):
							printL (( "Failed to add block by m_add_block, re-requesting the block #",blocknumber ))
							pending_blocks = {}
							fork.fork_recovery(i-1, chain, randomize_headerhash_fetch)		
							return
					f.sync = 0
					last_bk_time = time.time()
					chain.state.update('unsynced')
					restart_monitor_bk()
			else:
				printL (( 'Didnt match', pending_blocks[block.blockheader.blocknumber][0], thisPeerHost.host, thisPeerHost.port ))

		except:
			printL(( '.block rejected - unable to decode serialised data', self.transport.getPeer().host))
			return

	def PH(self, data):
		if chain.state.current == 'forked':
			fork.verify(data, self.identity, chain, randomize_headerhash_fetch)
		else:
			mini_block = json.loads(data)
			self.blocknumber_headerhash[mini_block['blocknumber']] = mini_block['headerhash']

	def LB(self):			#request for last block to be sent
		printL(( '<<<Sending last block', str(chain.m_blockheight()), str(len(chain.json_bytestream(chain.m_get_last_block()))),' bytes', 'to node: ', self.transport.getPeer().host))
		self.transport.write(self.wrap_message('BK',chain.json_bytestream_bk(chain.m_get_last_block())))
		return

	def MB(self):		#we send with just prefix as request..with CB number and blockhash as answer..
		printL(( '<<<Sending blockheight to:', self.transport.getPeer().host, str(time.time())))
		self.send_m_blockheight_to_peer()
		return
			
	def CB(self, data):
		z = chain.json_decode(data)
		block_number = z['block_number']
		headerhash = z['headerhash'].encode('latin1')
				
		self.blockheight = block_number
				
		printL(( '>>>Blockheight from:', self.transport.getPeer().host, 'blockheight: ', block_number, 'local blockheight: ', str(chain.m_blockheight()), str(time.time())))

		self.factory.peers_blockheight[self.transport.getPeer().host + ':' + str(self.transport.getPeer().port)] = z['block_number']

		if chain.state.current == 'syncing': return

		if block_number == chain.m_blockheight():
			if chain.m_blockchain[block_number].blockheader.headerhash != headerhash:
				printL(( '>>> WARNING: headerhash mismatch from ', self.transport.getPeer().host))
				
				# initiate fork recovery and protection code here..
				# call an outer function which sets a flag and scrutinises the chains from all connected hosts to see what is going on..
				# again need to think this one through in detail..
						
				return

		if block_number > chain.m_blockheight():		
			return

		if len(chain.m_blockchain) == 1 and self.factory.genesis == 0:
			self.factory.genesis = 1										# set the flag so that no other Protocol instances trigger the genesis stake functions..
			printL(( 'genesis pos countdown to block 1 begun, 60s until stake tx circulated..'))
			reactor.callLater(1, pre_pos_1)
			return
				
		elif len(chain.m_blockchain) == 1 and self.factory.genesis == 1:	#connected to multiple hosts and already passed through..
			return

	def BN(self, data):			#request for block (n)
		if int(data) <= chain.m_blockheight():
			printL(( '<<<Sending block number', str(int(data)), str(len(chain.json_bytestream(chain.m_get_block(int(data))))),' bytes', 'to node: ', self.transport.getPeer().host))
			self.transport.write(self.wrap_message('BK',chain.json_bytestream_bk(chain.m_get_block(int(data)))))
			return
		else:
			if int(data) >= chain.m_blockheight():
				printL(( 'BN for a blockheight greater than local chain length..'))
				return
			else:
				printL(( 'BN request without valid block number', data, '- closing connection'))
				self.transport.loseConnection()
				return
		
	def FB(self, data):		#Fetch Request for block
		data = int(data)
		if data > 0 and data <= chain.height():
			printL(( '<<<Pushing block number', str(data), str(len(chain.json_bytestream(chain.m_get_block(data)))),' bytes', 'to node: ', self.transport.getPeer().host ))
			self.transport.write(self.wrap_message('PB',chain.json_bytestream_pb(chain.m_get_block(data))))
		else:
			if data > chain.height():
				printL(( 'FB for a blocknumber is greater than the local chain length..' ))
				return

	def FH(self, data):		#Fetch Block Headerhash
		data = int(data)
		if data > 0 and data <= chain.height():
			mini_block = {}
			printL(( '<<<Pushing block headerhash of block number ', str(data), ' to node: ', self.transport.getPeer().host ))
			mini_block['headerhash'] = chain.m_get_block(data).blockheader.headerhash
			mini_block['blocknumber'] = data
			self.transport.write(self.wrap_message('PH',chain.json_bytestream_ph(mini_block)))
		else:
			if data > chain.height():
				printL(( 'FH for a blocknumber is greater than the local chain length..' ))
				return

	def PO(self, data):
		if data[0:2] == 'NG':
			y = 0
			for entry in chain.ping_list:
				if entry['node'] == self.transport.getPeer().host:
					entry['ping (ms)'] = (time.time()-chain.last_ping)*1000
					y = 1
			if y == 0:
				chain.ping_list.append({'node': self.transport.getPeer().host, 'ping (ms)' : (time.time()-chain.last_ping)*1000})

	def PI(self, data):
		if data[0:2] == 'NG':
			self.transport.write(self.wrap_message('PONG'))
		else:
			self.transport.loseConnection()
			return

	def PL(self, data):			#receiving a list of peers to save into peer list..
		self.recv_peers(data)

	def RT(self):
		'<<< Transaction_pool to peer..'
		for t in chain.transaction_pool:
			f.send_tx_to_peers(t)
		return

	def PE(self):			#get a list of connected peers..need to add some ddos and type checking proteection here..
		self.get_peers()

	def VE(self, data=None):
		if not data:
			self.transport.write(self.wrap_message('VE',chain.version_number))
		else:
			self.version = str(data)
			printL(( self.transport.getPeer().host, 'version: ', data))
		return

	def R1(self, data):							#receive a reveal_one message sent out after block receipt or creation (could be here prior to the block!)

		z = chain.json_decode(data)
		if not z:
			return
		block_number = z['block_number']
		headerhash = z['headerhash'].encode('latin1')
		stake_address = z['stake_address'].encode('latin1')
		reveal_one = z['reveal_one'].encode('latin1')
		reveal_two = z['reveal_two'].encode('latin1')

		if chain.is_stake_banned(stake_address):
			printL (( 'Rejecting R1 as peer is in banned list ',stake_address, ' ',self.transport.getPeer().host, ':', self.transport.getPeer().port ))
			return

		if block_number<=chain.m_blockheight():
			return

		for entry in chain.stake_reveal_one:	#already received, do not relay.
			if entry[3] == reveal_one:
				return

		if len(chain.stake_validator_latency) > 20:
			del chain.stake_validator_latency[min(chain.stake_validator_latency.keys())]
		# is reveal_one valid - does it hash to terminator in stake_list? We check that headerhash+block_number match in reveal_two_logic

		tmp = sha256(reveal_one)
		y=0
		if chain.state.epoch_diff == 0:
			for s in chain.stake_list_get():
				if s[0] == stake_address:
					y=1
					epoch = block_number/common.EPOCH_SIZE()			#+1 = next block
					for x in range(block_number-(epoch*common.EPOCH_SIZE())):	
						tmp = sha256(tmp)
					if tmp != s[1]:
						printL(( self.identity, ' reveal doesnt hash to stake terminator', 'reveal', reveal_one, 'nonce', s[2], 'hash_term', s[1]))
						return
			if y==0:
				printL(( 'stake address not in the stake_list'))
				return

		if len(r1_time_diff)>2:
			del r1_time_diff[min(r1_time_diff.keys())]				

		r1_time_diff[block_number].append(int(time.time()*1000))

		printL(( '>>> POS reveal_one:', self.transport.getPeer().host, stake_address, str(block_number), reveal_one))
				
		chain.stake_reveal_one.append([stake_address, headerhash, block_number, reveal_one, reveal_two]) 

		if chain.state.current == 'synced':
			for peer in self.factory.peers:
				if peer != self:
					peer.transport.write(self.wrap_message('R1',chain.json_encode(z)))	#relay
			
		return

	def R2(self, data):
		z = chain.json_decode(data)
		if not z:
			return

		block_number = z['block_number']
		headerhash = z['headerhash'].encode('latin1')
		stake_address = z['stake_address'].encode('latin1')
		reveal_one = z['reveal_one'].encode('latin1')
		nonce = z['nonce'].encode('latin1')
		winning_hash = z['winning_hash'].encode('latin1')
		reveal_three = z['reveal_three'].encode('latin1')

		if chain.is_stake_banned(stake_address):
			printL (( 'Rejecting R2 as peer is in banned list ', stake_address ))
			return

		if block_number<=chain.m_blockheight():
			return

		for entry in chain.stake_reveal_two:	#already received, do not relay.
			if entry[4] == nonce:
				return

		# add code to accept only R2's which are at R1 level..

		# is reveal_two valid, is there an equivalent reveal_one entry for this block?

		if chain.state.epoch_diff == 0:
			if sha256(reveal_one+nonce) not in [s[4] for s in chain.stake_reveal_one]:
				printL(( 'reveal_two not sha256(reveal_one+nonce) in chain.stake_reveal_one ', self.identity))
				return

		r2_time_diff[block_number].append(int(time.time()*1000))

		if len(r2_time_diff)>20:
			del r2_time_diff[min(r2_time_diff.keys())]				


		if stake_address not in chain.stake_validator_latency[block_number]:
			chain.stake_validator_latency[block_number][stake_address] = {}

		chain.stake_validator_latency[block_number][stake_address]['r1_time_diff'] = z['r1_time_diff']

		printL(( '>>> POS reveal_two', self.transport.getPeer().host, stake_address, str(block_number), reveal_one, winning_hash))

		#chain.stake_reveal_two.append([z['stake_address'],z['headerhash'], z['block_number'], z['reveal_one'], z['nonce']], z['winning_hash'], z['reveal_three']])		#don't forget to store our reveal in stake_reveal_one

		chain.stake_reveal_two.append([stake_address, headerhash, block_number, reveal_one, nonce, winning_hash, reveal_three]) 

		if chain.state.current == 'synced':
			for peer in self.factory.peers:
				if peer != self:
					peer.transport.write(self.wrap_message('R2',chain.json_encode(z)))	#relay
		return

	def R3(self, data):
		z = chain.json_decode(data)
		if not z:
			return

		#chain.stake_reveal_three.append([z['stake_address'],z['headerhash'], z['block_number'], z['consensus_hash'], z['nonce2']])

		stake_address = z['stake_address'].encode('latin1')
		headerhash = z['headerhash'].encode('latin1')
		block_number = z['block_number']
		consensus_hash = z['consensus_hash'].encode('latin1')
		nonce2 = z['nonce2'].encode('latin1')

		if chain.is_stake_banned(stake_address):
			printL (( 'Rejecting R3 as peer is in banned list ', stake_address ))
			return

		if block_number<=chain.m_blockheight():
			return

		for entry in chain.stake_reveal_three:		# we have already seen the message..
			if entry[4] == nonce2:
				return

		y=0
		if chain.state.epoch_diff == 0:
			for s in chain.stake_reveal_two:
				if s[0] == stake_address and s[1] == headerhash and s[2] == block_number:
					if s[6] == sha256(s[4]+nonce2):
						y=1
			if y == 0:
				printL(('reveal_three does not match sha256(nonce+nonce2 ', self.identity))
				return

		if stake_address not in chain.stake_validator_latency[block_number]:
			chain.stake_validator_latency[block_number][stake_address] = {}
		chain.stake_validator_latency[block_number][stake_address]['r2_time_diff'] = z['r2_time_diff']

		printL(('>>> POS reveal_three', self.transport.getPeer().host, stake_address, str(block_number), consensus_hash))
		chain.stake_reveal_three.append([stake_address, headerhash, block_number, consensus_hash, nonce2, self])
		if chain.state.current == 'synced':
			for peer in self.factory.peers:
				if peer != self:
					peer.transport.write(self.wrap_message('R3',chain.json_encode(z)))
		return

														# could add a ttl on this..so runs around the network triggering ip calls then dissipates..or single time based bloom.

	def IP(self, data):								#fun feature to allow geo-tagging on qrl explorer of test nodes..reveals IP so optional..
		if not data:
			if self.factory.ip_geotag == 1:
				for peer in self.factory.peers:
					if peer != self:
						peer.transport.write(self.wrap_message('IP',self.transport.getHost().host))
		else:
			if data not in chain.ip_list:
				chain.ip_list.append(data)
				for peer in self.factory.peers:
					if peer != self:
						peer.transport.write(self.wrap_message('IP',self.transport.getHost().host))

		return


	def recv_peers(self, json_data):
		data = chain.json_decode(json_data)
		new_ips = []
		for ip in data:
				new_ips.append(ip.encode('latin1'))
		peers_list = chain.state_get_peers()
		printL(( self.transport.getPeer().host, 'peers data received: ', new_ips))
		for node in new_ips:
				if node not in peers_list:
					if node != self.transport.getHost().host:
						peers_list.append(node)
						reactor.connectTCP(node, PEER_PORT, f)
		chain.state_put_peers(peers_list)
		chain.state_save_peers()
		return

	def get_latest_block_from_connection(self):
		printL(( '<<<Requested last block from', self.transport.getPeer().host))
		self.transport.write(self.wrap_message('LB'))
		return

	def get_m_blockheight_from_connection(self):
		printL(( '<<<Requesting blockheight from', self.transport.getPeer().host))
		self.transport.write(self.wrap_message('MB'))
		return

	def send_m_blockheight_to_peer(self):
		z = {}
		z['headerhash'] = chain.m_blockchain[-1].blockheader.headerhash				
		z['block_number'] = chain.m_blockchain[-1].blockheader.blocknumber 			
		self.transport.write(self.wrap_message('CB',chain.json_encode(z)))
		return

	def get_version(self):
		printL(( '<<<Getting version', self.transport.getPeer().host))
		self.transport.write(self.wrap_message('VE'))
		return

	def get_peers(self):
		printL(( '<<<Sending connected peers to', self.transport.getPeer().host))
		peers_list = []
		for peer in self.factory.peers:
			peers_list.append(peer.transport.getPeer().host)
		self.transport.write(self.wrap_message('PL',chain.json_encode(peers_list)))
		return

	def get_block_n(self, n):
		printL(( '<<<Requested block: ', str(n), 'from ', self.transport.getPeer().host))
		self.transport.write(self.wrap_message('BN',str(n)))
		return

	def fetch_block_n(self, n):
		printL(( '<<<Fetching block: ', n, 'from ', self.transport.getPeer().host, ':', self.transport.getPeer().port ))
		self.transport.write(self.wrap_message('FB',str(n)))
		return

	def fetch_headerhash_n(self, n):
		printL(( '<<<Fetching headerhash of block: ', n, 'from ', self.transport.getPeer().host, ':', self.transport.getPeer().port ))
		self.transport.write(self.wrap_message('FH',str(n)))
		return

	def wrap_message(self, type, data=None):
		jdata = {}
		jdata['type'] = type
		if data:
			jdata['data'] = data
		str_data = json.dumps(jdata)
		return chr(255)+chr(0)+chr(0)+struct.pack('>L', len(str_data))+chr(0)+str_data+chr(0)+chr(0)+chr(255)

	def clean_buffer(self, reason=None, upto=None):
		if reason:
			printL(( reason))
		if upto:
			self.buffer = self.buffer[upto:] 			#Clean buffer till the value provided in upto
		else:
			self.buffer = ''					#Clean buffer completely

	def parse_buffer(self):
		if len(self.buffer)==0:
			return False

		d = self.buffer.find(chr(255)+chr(0)+chr(0))					#find the initiator sequence
		num_d = self.buffer.count(chr(255)+chr(0)+chr(0))				#count the initiator sequences

		if d == -1:														#if no initiator sequences found then wipe buffer..
			self.clean_buffer(reason='Message data without initiator')
			return False

		self.buffer = self.buffer[d:]									#delete data up to initiator

		if len(self.buffer)<8:							#Buffer is still incomplete as it doesn't have message size
			return False

		try: m = struct.unpack('>L', self.buffer[3:7])[0]			#is m length encoded correctly?
		except:
				if num_d > 1:										#if not, is this the only initiator in the buffer?
					self.buffer = self.buffer[3:]
					d = self.buffer.find(chr(255)+chr(0)+chr(0))
					self.clean_buffer(reason='Struct.unpack error attempting to decipher msg length, next msg preserved', upto=d)		#no
					return True
				else:
					self.clean_buffer(reason='Struct.unpack error attempting to decipher msg length..')		#yes
				return False

		if m > 500*1024:							#check if size is more than 500 KB
			if num_d > 1:
				self.buffer = self.buffer[3:]
				d = self.buffer.find(chr(255)+chr(0)+chr(0))
				self.clean_buffer(reason='Size is more than 500 KB, next msg preserved', upto=d)
				return True
			else:
				self.clean_buffer(reason='Size is more than 500 KB')
			return False

		e = self.buffer.find(chr(0)+chr(0)+chr(255))				#find the terminator sequence

		if e ==-1:							#no terminator sequence found
			if len(self.buffer) > 8+m+3:
				if num_d >1:										#if not is this the only initiator sequence?
					self.buffer = self.buffer[3:]
					d = self.buffer.find(chr(255)+chr(0)+chr(0))
					self.clean_buffer(reason='Message without appropriate terminator, next msg preserved', upto=d)						#no
					return True
				else:
					self.clean_buffer(reason='Message without initiator and terminator')					#yes
			return False

		if e != 3+5+m:								#is terminator sequence located correctly?
			if num_d >1:											#if not is this the only initiator sequence?
				self.buffer = self.buffer[3:]
				d = self.buffer.find(chr(255)+chr(0)+chr(0))
				self.clean_buffer(reason='Message terminator incorrectly positioned, next msg preserved', upto=d)						#no
				return True
			else:
				self.clean_buffer(reason='Message terminator incorrectly positioned')						#yes
			return False

		self.messages.append(self.buffer[8:8+m])					#if survived the above then save the msg into the self.messages
		self.buffer = self.buffer[8+m+3:]							#reset the buffer to after the msg
		return True

	def dataReceived(self, data):		# adds data received to buffer. then tries to parse the buffer twice..

		self.buffer += data

		for x in range(50):
			if self.parse_buffer()==False:
				break
			else:
				for msg in self.messages:
					self.parse_msg(msg)
				del self.messages[:]
		return

	def connectionMade(self):
		peerHost, peerPort = self.transport.getPeer().host, self.transport.getPeer().port
		self.identity = peerHost+":"+str(peerPort)
		self.factory.connections += 1
		self.factory.peers.append(self)
		peer_list = chain.state_get_peers()
		if self.transport.getPeer().host == self.transport.getHost().host:
						if self.transport.getPeer().host in peer_list:
								printL(( 'Self in peer_list, removing..'))
								peer_list.remove(self.transport.getPeer().host)
								chain.state_put_peers(peer_list)
								chain.state_save_peers()
						self.transport.loseConnection()
						return
		
		if self.transport.getPeer().host not in peer_list:
			printL(( 'Adding to peer_list'))
			peer_list.append(self.transport.getPeer().host)
			chain.state_put_peers(peer_list)
			chain.state_save_peers()
		printL(( '>>> new peer connection :', self.transport.getPeer().host, ' : ', str(self.transport.getPeer().port)))

		self.get_m_blockheight_from_connection()
		self.get_peers()
		self.get_version()

		# here goes the code for handshake..using functions within the p2pprotocol class
		# should ask for latest block/block number.
		

	def connectionLost(self, reason):
		self.factory.connections -= 1
		printL(( self.transport.getPeer().host,  ' disconnnected. ', 'remainder connected: ', str(self.factory.connections))) #, reason 
		self.factory.peers.remove(self)
		host_port = self.transport.getPeer().host + ':' + str(self.transport.getPeer().port)
		if host_port in self.factory.peers_blockheight:
			del self.factory.peers_blockheight[host_port]
		if self.factory.connections == 0:
			stop_all_loops()
			reactor.callLater(60,f.connect_peers)

	

	def recv_tx(self, json_tx_obj):
		
		try: tx = chain.SimpleTransaction().json_to_transaction(json_tx_obj)
		except: 
				printL(( 'tx rejected - unable to decode serialised data - closing connection'))
				self.transport.loseConnection()
				return

		if tx.txhash in chain.prev_txpool or tx.txhash in chain.pending_tx_pool_hash:
			return

		del chain.prev_txpool[0]
		chain.prev_txpool.append(tx.txhash)
		
		for t in chain.transaction_pool:			#duplicate tx already received, would mess up nonce..
			if tx.txhash == t.txhash:
				return

		chain.update_pending_tx_pool(tx, self)
		
		return


class p2pFactory(ServerFactory):

	protocol = p2pProtocol

	def __init__(self):
		self.stake = True			#default to mining off as the wallet functions are not that responsive at present with it enabled..
		self.peers_blockheight = {}
		self.target_retry = defaultdict(int)
		self.peers = []
		self.target_peers = {}
		self.fork_target_peers = {}
		self.connections = 0
		self.buffer = ''
		self.sync = 0
		self.partial_sync = [0, 0]
		self.long_gap_block = 0
		self.mining = 0
		self.newblock = 0
		self.exit = 0
		self.genesis = 0
		self.missed_block = 0
		self.requested = [0, 0]
		self.ip_geotag = 1			# to be disabled in main release as reveals IP..
		self.last_reveal_one = None
		self.last_reveal_two = None
		self.last_reveal_three = None

# factory network functions
	
	def get_block_a_to_b(self, a, b):
		printL(( '<<<Requested blocks:', a, 'to ', b, ' from peers..'))
		l = range(a,b)
		for peer in self.peers:
			if len(l) > 0:
				peer.transport.write(self.f_wrap_message('BN',str(l.pop(0))))
			else:
				return				

	def get_block_n_random_peer(self,n):
		printL(( '<<<Requested block: ', n, 'from random peer.'))
		random.choice(self.peers).get_block_n(n)
		return


	def get_block_n(self, n):
		printL(( '<<<Requested block: ', n, 'from peers.'))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('BN',str(n)))
		return

	def get_m_blockheight_from_random_peer(self):
		printL(( '<<<Requested blockheight from random peer.'))
		random.choice(self.peers).get_m_blockheight_from_connection()
		return

	def get_blockheight_map_from_peers(self):
		printL(( '<<<Requested blockheight_map from peers.'))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('BM'))
		return

	def get_m_blockheight_from_peers(self):
		for peer in self.peers:
			peer.get_m_blockheight_from_connection()
		return

	def send_m_blockheight_to_peers(self):
		printL(( '<<<Sending blockheight to peers.'))
		for peer in self.peers:
			peer.send_m_blockheight_to_peer()
		return

	def f_wrap_message(self, type, data=None):
		jdata = {}
		jdata['type'] = type
		if data:
			jdata['data'] = data
		str_data = json.dumps(jdata)
		return chr(255)+chr(0)+chr(0)+struct.pack('>L', len(str_data))+chr(0)+str_data+chr(0)+chr(0)+chr(255)

	def send_st_to_peers(self, st):
		printL(( '<<<Transmitting ST:', st.hash))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('ST',st.transaction_to_json()))
		return

	def send_tx_to_peers(self, tx):
		printL(( '<<<Transmitting TX: ', tx.txhash))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('TX',tx.transaction_to_json()))
		return

	def send_reboot(self, json_hash):
		printL(( '<<<Transmitting Reboot Command' ))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('reboot', json_hash))
		return


	# transmit reveal_one hash.. (node cast lottery vote)

	def send_stake_reveal_one(self):
		
		z = {}
		z['stake_address'] = chain.mining_address
		z['headerhash'] = chain.m_blockchain[-1].blockheader.headerhash				#demonstrate the hash from last block to prevent building upon invalid block..
		z['block_number'] = chain.m_blockchain[-1].blockheader.blocknumber+1		#next block..
		epoch = z['block_number']/common.EPOCH_SIZE()			#+1 = next block
		z['reveal_one'] = chain.hash_chain[:-1][::-1][z['block_number']-(epoch*common.EPOCH_SIZE())]	
		rkey = random_key()
		z['reveal_two'] = sha256(z['reveal_one']+rkey)

		y=False
		tmp_stake_reveal_one = []
		for r in chain.stake_reveal_one:											#need to check the reveal list for existence already, if so..reuse..
			if r[0] == chain.mining_address:
				if r[1] == z['headerhash']:
					if r[2] == z['block_number']:
						if y==True:
							continue						#if repetition then remove..
						else:
							z['reveal_one'] = r[3]
							z['reveal_two'] = r[4]
							y=True
			tmp_stake_reveal_one.append(r)
		
		chain.stake_reveal_one = tmp_stake_reveal_one
		printL(( '<<<Transmitting POS reveal_one'))

		self.last_reveal_one = z
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('R1',chain.json_encode(z)))
		
		if y==False:
			chain.stake_reveal_one.append([z['stake_address'],z['headerhash'], z['block_number'], z['reveal_one'], z['reveal_two'], rkey])		#don't forget to store our reveal in stake_reveal_one
		return


	def send_last_stake_reveal_one(self):
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('R1',chain.json_encode(self.last_reveal_one)))

	# transmit reveal_two hash.. (node cast network winning vote)

	def send_stake_reveal_two(self, winning_hash):
		printL(( '<<<Transmitting POS reveal_two'))
		
		z = {}
		z['stake_address'] = chain.mining_address
		z['headerhash'] = chain.m_blockchain[-1].blockheader.headerhash				#demonstrate the hash from last block to prevent building upon invalid block..
		z['block_number'] = chain.m_blockchain[-1].blockheader.blocknumber+1		#next block..
		epoch = z['block_number']/common.EPOCH_SIZE()			#+1 = next block
		z['reveal_one'] = chain.hash_chain[:-1][::-1][z['block_number']-(epoch*common.EPOCH_SIZE())]	
		global r1_time_diff
		z['r1_time_diff'] = r1_time_diff[z['block_number']]

		for s in chain.stake_reveal_one:
			if len(s)==6:
				if s[3]==z['reveal_one']:			#consider adding checks here..
					rkey = s[5]
		z['nonce'] = rkey
		z['winning_hash'] = winning_hash

		rkey2 = random_key()
		z['reveal_three'] = sha256(z['nonce']+rkey2)

		self.last_reveal_two = z
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('R2',chain.json_encode(z)))
		
		chain.stake_reveal_two.append([z['stake_address'], z['headerhash'], z['block_number'], z['reveal_one'], z['nonce'], z['winning_hash'], z['reveal_three'], rkey2])		#don't forget to store our reveal in stake_reveal_one
		return

	def send_last_stake_reveal_two(self):
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('R2',chain.json_encode(self.last_reveal_two)))


	# transmit reveal_three hash..	(node cast network consensus vote)		(cryptographically linked to reveal_two by R2: hash(nonce+nonce2) -> reveal_three)

	def send_stake_reveal_three(self, consensus_hash):
		printL(('<<<Transmitting POS reveal_three'))

		z = {}
		z['stake_address'] = chain.mining_address
		z['headerhash'] = chain.m_blockchain[-1].blockheader.headerhash
		z['block_number'] = chain.m_blockchain[-1].blockheader.blocknumber+1
		z['consensus_hash'] = consensus_hash
		global r2_time_diff
		z['r2_time_diff'] = r2_time_diff[z['block_number']]
		for s in chain.stake_reveal_two:
			if len(s)==8:
				if sha256(s[4]+s[7]) == s[6]:
					rkey2 = s[7]
		
		z['nonce2'] = rkey2

		self.last_reveal_three = z
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('R3',chain.json_encode(z)))

		chain.stake_reveal_three.append([z['stake_address'], z['headerhash'], z['block_number'], z['consensus_hash'], z['nonce2'], None])
		return

	def send_last_stake_reveal_three(self):
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('R3',chain.json_encode(self.last_reveal_three)))

	def ip_geotag_peers(self):
		printL(( '<<<IP geotag broadcast'))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('IP'))
		return


	def ping_peers(self):
		printL(( '<<<Transmitting network PING'))
		chain.last_ping = time.time()
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('PING'))
		return

	# send POS block to peers..

	def send_stake_block(self, block_obj):
		printL(( '<<<Transmitting POS created block', str(block_obj.blockheader.blocknumber), block_obj.blockheader.headerhash))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('S4',chain.json_bytestream(block_obj)))
		return

	# send/relay block to peers

	def send_block_to_peers(self, block):
		printL(( '<<<Transmitting block: ', block.blockheader.headerhash))
		for peer in self.peers:
			peer_info = peer.transport.getPeer()
			printL (('<<<Block Transmitted to ', peer_info.host, ':', peer_info.port ))
			peer.transport.write(self.f_wrap_message('BK',chain.json_bytestream_bk(block)))
		return

	# request transaction_pool from peers

	def get_tx_pool_from_peers(self):
		printL(( '<<<Requesting TX pool from peers..'))
		for peer in self.peers:
			peer.transport.write(self.f_wrap_message('RT'))
		return

# connection functions

	def connect_peers(self):
		printL(( '<<<Reconnecting to peer list:'))
		for peer in chain.state_get_peers():
			reactor.connectTCP(peer, PEER_PORT, f)

	def clientConnectionLost(self, connector, reason):		#try and reconnect
		#printL(( 'connection lost: ', reason, 'trying reconnect'
		#connector.connect()
		return

	def clientConnectionFailed(self, connector, reason):
		#printL(( 'connection failed: ', reason
		return

	def startedConnecting(self, connector):
		#printL(( 'Started to connect.', connector
		return


class WalletFactory(ServerFactory):

	protocol = WalletProtocol

	def __init__(self, stuff):
		self.newaddress = 0
		self.stuff = stuff
		self.recn = 0
		self.maxconnections = 1
		self.connections = 0
		self.last_cmd = 'help'

class ApiFactory(ServerFactory):

	protocol = ApiProtocol

	def __init__(self):
		self.connections = 0
		self.api = 1
		pass

if __name__ == "__main__":
	start_time = time.time()
	printL(( 'Reading chain..'))
	chain.m_load_chain()
	printL(( str(len(chain.m_blockchain))+' blocks'))
	printL(( 'Verifying chain'))
	#chain.state_add_block(m_blockchain[1])
	#chain.m_verify_chain(verbose=1)
	printL(( 'Building state leveldb' ))
	#chain.state_read_chain()
	if chain.verify_chain() is False:
		printL(( 'verify_chain() failed..'))
		exit()
	printL(( 'Loading node list..'))			# load the peers for connection based upon previous history..
	chain.state_load_peers()
	printL(( chain.state_get_peers()))

	stuff = 'QRL node connection established. Try starting with "help"'+'\r\n'
	printL(( '>>>Listening..'))
	
	f = p2pFactory()
	api = ApiFactory()

	reactor.listenTCP(TELNET_PORT, WalletFactory(stuff), interface='127.0.0.1')
	reactor.listenTCP(PEER_PORT, f)
	reactor.listenTCP(API_PORT, api)

	restart_monitor_bk()

	printL(( 'Connect to the node via telnet session on port ' + str(TELNET_PORT) + ': i.e "telnet localhost ' + str(TELNET_PORT) + '"'))
	printL(( '<<<Connecting to nodes in peer.dat'))

	f.connect_peers()
	schedule_peers_blockheight()
	#if not (chain.mining_address in chain.m_blockchain[0].stake_list and len(chain.m_blockchain)==1):
	#	fork.fork_recovery(len(chain.m_blockchain)+1, chain, randomize_headerhash_fetch)
	reactor.run()
