#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#
#   ARD - Automatic Reaction Discovery
#
#   Copyright (c) 2016 Prof. William H. Green (whgreen@mit.edu) and Colin
#   Grambow (cgrambow@mit.edu)
#
#   Permission is hereby granted, free of charge, to any person obtaining a
#   copy of this software and associated documentation files (the "Software"),
#   to deal in the Software without restriction, including without limitation
#   the rights to use, copy, modify, merge, publish, distribute, sublicense,
#   and/or sell copies of the Software, and to permit persons to whom the
#   Software is furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#   FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
#   DEALINGS IN THE SOFTWARE.
#
###############################################################################

"""
Contains functions and classes for generating 3D geometries using Open Babel.
Also contains functionality for estimating thermo using group additivity and
RMG database values.
"""

from __future__ import division

import math
import os

import pybel
from rmgpy import settings
from rmgpy.species import Species
from rmgpy.data.thermo import ThermoDatabase

import constants
import props
from node import Node
from quantum import QuantumError

###############################################################################

def readstring(format, string):
    """
    Read in a molecule from a string and convert to a :class:`Molecule` object.
    """
    mol = pybel.readstring(format, string)
    return Molecule(mol.OBMol)

def make3DandOpt(mol, forcefield='mmff94', make3D=True):
    """
    Generate 3D coordinates and optimize them using a force field.
    """
    if make3D:
        mol.make3D(forcefield=forcefield)
    mol.localopt(forcefield=forcefield)

def makeMolFromAtomsAndBonds(atoms, bonds, spin=None):
    """
    Create a new Molecule object from a sequence of atoms and bonds.
    """
    mol = Molecule(pybel.ob.OBMol())
    OBMol = mol.OBMol

    for atomicnum in atoms:
        a = pybel.ob.OBAtom()
        a.SetAtomicNum(atomicnum)
        OBMol.AddAtom(a)
    for bond in bonds:
        OBMol.AddBond(bond[0] + 1, bond[1] + 1, bond[2])

    mol.assignSpinMultiplicity()
    if spin is not None:
        OBMol.SetTotalSpinMultiplicity(spin)
    OBMol.SetHydrogensAdded()

    return mol

# def genConformers(mol, forcefield='mmff94', nconf_max=3, steps=1000):
#     """
#     Generate conformers of `mol` using a weighted rotor search. This assumes
#     that the molecule has coordinates and that they are optimized.
#     """
#     ff = pybel.ob.OBForceField.FindForceField(forcefield)
#     numrots = mol.OBMol.NumRotors()
#     if numrots > 0:
#         nconf = min(numrots, nconf_max)
#         ff.WeightedRotorSearch(nconf, int(math.log(numrots + 1) * steps))
#         ff.GetConformers(mol.OBMol)

def setLowestEnergyConformer(mol, forcefield='mmff94', steps=2000):
    """
    Set the coordinates of `mol` to the lowest energy conformer as determined
    by a weighted rotor search.
    """
    ff = pybel.ob.OBForceField.FindForceField(forcefield)
    numrots = mol.OBMol.NumRotors()
    if numrots > 0:
        ff.WeightedRotorSearch(numrots + 2, int(math.log(numrots + 1) * steps))
        ff.GetCoordinates(mol.OBMol)

###############################################################################

class Molecule(pybel.Molecule):
    """
    Extension of :class:`pybel.Molecule` for the generation of 3D geometries
    for structures containing more than one molecule.
    The attributes are:

    =============== ======================== ==================================
    Attribute       Type                     Description
    =============== ======================== ==================================
    `OBMol`         :class:`pybel.ob.OBMol`  An Open Babel molecule object
    `label`         ``str``                  A label for the molecule
    `mols`          ``list``                 A list of :class:`Molecule` molecules contained in `self`
    `mols_indices`  ``list``                 Tuple of lists containing indices of atoms in the molecules
    =============== ======================== ==================================

    Note: The molecule should have all hydrogen atoms explicitly assigned. If
    this is not the case, then segmentation faults may occur.
    """

    def __init__(self, OBMol):
        super(Molecule, self).__init__(OBMol)
        self.label = None
        self.mols_indices = None
        self.mols = None

    def copy(self):
        """
        Create copy of `self`. The copy is somewhat reduced in that it only
        contains atoms and bonds.
        """
        # Create new empty instance
        m = Molecule(pybel.ob.OBMol())
        OBMol = m.OBMol

        for atom in self:
            OBMol.AddAtom(atom.OBAtom)
        for bond in pybel.ob.OBMolBondIter(self.OBMol):
            OBMol.AddBond(bond)

        OBMol.SetTotalSpinMultiplicity(self.spin)
        OBMol.SetHydrogensAdded()

        m.mols_indices = self.mols_indices
        m.mols = self.mols
        return m

    def toNode(self):
        """
        Convert to :class:`node.Node` object and return the object.
        """
        atoms = []
        coords = []
        for atom in self:
            atoms.append(atom.atomicnum)
            coords.append(atom.coords)
        node = Node(coords, atoms, self.spin)
        node.energy = self.energy
        return node

    def toAdjlist(self):
        """
        Convert to adjacency list as used in the RMG software.
        """
        # Dictionary of RMG bond types
        bondtypes = {1: 'S', 2: 'D', 3: 'T'}

        adjlist = 'name\n'
        adjlist += 'multiplicity {}\n'.format(self.spin)

        for atom in self:
            number = atom.idx
            label = ''
            element = props.atomnum[atom.atomicnum]
            spin = atom.spin
            if spin == 0:
                spin = 1
            unpaired = spin - 1
            pairs = (props.valenceelec[atom.atomicnum] - atom.OBAtom.BOSum() - unpaired) // 2
            charge = atom.formalcharge

            bondlist = ''
            for bond in pybel.ob.OBAtomBondIter(atom.OBAtom):
                other_atom = bond.GetBeginAtomIdx()
                if other_atom == number:
                    other_atom = bond.GetEndAtomIdx()
                bondtype = bondtypes[bond.GetBondOrder()]
                bondlist += ' {{{},{}}}'.format(other_atom, bondtype)

            adjlist += '{:<2} {} {} u{} p{} c{}{}\n'.format(number, label, element, unpaired, pairs, charge, bondlist)

        return adjlist

    def toRMGSpecies(self):
        """
        Convert to :class:`rmgpy.species.Species` object and return the object.
        """
        adjlist = self.toAdjlist()
        spc = Species().fromAdjacencyList(adjlist)
        spc.label = ''
        return spc

    def assignSpinMultiplicity(self):
        """
        Assigns the spin multiplicity of all atoms based on connectivity. This
        function assumes that all hydrogens are specified explicitly.
        """
        self.OBMol.SetSpinMultiplicityAssigned()
        num_diff = 0  # Number of times that a multiplicity greater than 1 occurs
        maxspin = 1

        for atom in self:
            OBAtom = atom.OBAtom
            # Only based on difference between expected and actual valence
            nonbond_elec = props.valenceelec[atom.atomicnum] - OBAtom.BOSum()

            # Tries to pair all electrons that are not involved in bonds, which means that singlet states are favored
            diff = nonbond_elec % 2

            if diff:
                num_diff += 1
                spin = diff + 1
                if spin > maxspin:
                    maxspin = spin
                OBAtom.SetSpinMultiplicity(spin)

        # Try to infer total spin multiplicity based on atom spins (assuming that spins in diradicals and higher are
        # opposite and favor the singlet state)
        if num_diff % 2 != 0:
            self.OBMol.SetTotalSpinMultiplicity(maxspin)
        else:
            self.OBMol.SetTotalSpinMultiplicity(1)

    def AssignSpinMultiplicity(self):
        """
        Override method in parent class.
        """
        self.assignSpinMultiplicity()

    def getH298(self, thermo_db=None):
        """
        Compute and return the standard enthalpy of formation of the structure
        in kcal/mol. A :class:`rmgpy.data.thermo.ThermoDatabase` instance can
        be supplied, which is used to search databases and use group additivity
        values.
        """
        # Load thermo database
        if thermo_db is None:
            thermo_db = ThermoDatabase()
            thermo_db.load(os.path.join(settings['database.directory'], 'thermo'))

        # Compute enthalpy for each molecule and add together
        H298 = 0.0
        self.separateMol()
        for mol in self.mols:
            spc = mol.toRMGSpecies()
            spc.thermo = thermo_db.getThermoData(spc)
            H298 += spc.thermo.H298.value_si / constants.kcal_to_J

        # Return combined enthalpy of all molecules
        return H298

    def setCoordsFromMol(self, other):
        """
        Set the coordinates for each atom in the current molecule from the
        atoms in another one.
        """
        if len(self.atoms) != len(other.atoms):
            raise Exception('Number of atoms must match')

        for atom, other_atom in zip(self, other):
            coord_vec = other_atom.OBAtom.GetVector()
            atom.OBAtom.SetVector(coord_vec)

    def isCarbeneOrNitrene(self):
        """
        Return a boolean indicating whether or not the molecule is a carbene or
        a nitrene.
        """
        for atom in self:
            OBAtom = atom.OBAtom

            if OBAtom.IsCarbon() and OBAtom.BOSum() == 2:
                return True
            if OBAtom.IsNitrogen() and OBAtom.BOSum() == 1:
                return True

        return False

    def optimizeGeometry(self, Qclass, **kwargs):
        """
        Perform a geometry optimization of each molecule in self using an
        electronic structure program specified in `Qclass` and a subsequent
        frequency calculation with the parameters specified in `kwargs`. Update
        the coordinates and energy, and return the number of gradient
        evaluations.
        """
        self.separateMol()

        if len(self.mols) > 1:
            ngrad = 0
            energy = 0.0
            name_base = kwargs.get('name', 'opt')
            err = False

            for i, mol in enumerate(self.mols):
                kwargs['name'] = name_base + str(i)
                node = mol.toNode()

                try:
                    ngrad += node.optimizeGeometry(Qclass, **kwargs)
                except QuantumError as e:
                    err, msg = True, e

                energy += node.energy
                mol_opt = node.toPybelMol()
                mol.setCoordsFromMol(mol_opt)

                self.mergeMols()

            # Raise error even if only one of the optimizations failed
            if err:
                raise QuantumError(msg)
        else:
            node = self.toNode()
            ngrad = node.optimizeGeometry(Qclass, **kwargs)
            energy = node.energy
            mol_opt = node.toPybelMol()
            self.setCoordsFromMol(mol_opt)

        self.OBMol.SetEnergy(energy)
        return ngrad

    def gen3D(self, forcefield='mmff94', d=3.5, make3D=True):
        """
        Generate 3D coordinates using the specified force field. If there are
        multiple molecules, they are separated by a distance of `d` in
        Angstrom.
        """
        spin = self.spin
        self.separateMol()

        # Arrange molecules in space and generate 3D geometries separately
        if len(self.mols) > 1:
            arrange3D = Arrange3D(self.mols)
            arrange3D.arrangeIn3D(forcefield=forcefield, d=d, make3D=make3D)

            self.mergeMols()
            self.OBMol.SetTotalSpinMultiplicity(spin)
        else:
            make3DandOpt(self, forcefield=forcefield, make3D=make3D)
            setLowestEnergyConformer(self, forcefield=forcefield)

    def separateMol(self):
        """
        Separate molecule based on the indices in `self.mols_indices`.
        """
        if self.mols is None:
            if self.mols_indices is None:
                self.connectivityAnalysis()

            nmols = len(self.mols_indices)
            if nmols > 1:
                self.mols = []
                for mol_idx in range(nmols):
                    mol = self.copy()

                    # Obtain indices of all atoms to be deleted for current molecule and obtain corresponding atoms
                    del_indices = [atom_idx for mol_idx_2, mol_indices in enumerate(self.mols_indices)
                                   if mol_idx_2 != mol_idx
                                   for atom_idx in mol_indices]
                    del_atoms = [mol.atoms[idx].OBAtom for idx in del_indices]

                    # Delete atoms not in current molecule
                    for atom in del_atoms:
                        mol.OBMol.DeleteAtom(atom)

                    mol.assignSpinMultiplicity()  # Has to be set again because
                    mol.OBMol.SetHydrogensAdded()
                    self.mols.append(mol)
            else:
                self.mols = [self]

    def mergeMols(self):
        """
        Merge molecules by clearing the current molecule and rewriting all
        atoms and bonds. The atoms are reordered according to the indices in
        `self.mols_indices`.
        """
        if self.mols is not None:
            spin = self.spin
            self.OBMol.Clear()

            if self.mols_indices is None:
                self.connectivityAnalysis()

            # Loop through molecules and append atoms and bonds in order
            natoms = 0
            for mol in self.mols:
                for atom in mol:
                    self.OBMol.AddAtom(atom.OBAtom)
                for bond in pybel.ob.OBMolBondIter(mol.OBMol):
                    self.OBMol.AddBond(
                        bond.GetBeginAtomIdx() + natoms, bond.GetEndAtomIdx() + natoms, bond.GetBondOrder()
                    )
                natoms += len(mol.atoms)

            # Reorder atoms
            mols_indices_new = [atom_idx for mol_indices in self.mols_indices for atom_idx in mol_indices]
            neworder = natoms * [0]
            for i, atom_idx in enumerate(mols_indices_new):
                neworder[atom_idx] = i + 1
            self.OBMol.RenumberAtoms(neworder)

            self.OBMol.SetHydrogensAdded()
            self.OBMol.SetTotalSpinMultiplicity(spin)

    def connectivityAnalysis(self):
        """
        Analyze bonds to determine which atoms are connected and form a
        molecule.
        """
        # Extract bonds
        bonds = [[bond.GetBeginAtomIdx() - 1, bond.GetEndAtomIdx() - 1] for bond in pybel.ob.OBMolBondIter(self.OBMol)]

        # Create first molecular fragment from first bond and start keeping track of atoms
        molecules = [bonds[0][:]]
        atoms_used = bonds[0][:]

        # Loop over remaining bonds
        for bond in bonds[1:]:
            ind1, ind2 = -1, -2

            for idx, molecule in enumerate(molecules):
                if bond[0] in molecule:
                    ind1 = idx
                if bond[1] in molecule:
                    ind2 = idx

            # Skip bond if both atoms are already contained in the same molecule
            if ind1 == ind2:
                continue
            # Combine fragments if they are connected through bond
            if ind1 != -1 and ind2 != -2:
                molecules[ind1].extend(molecules[ind2])
                del molecules[ind2]
            # Add new atom to fragment if it is connected
            elif ind1 != -1:
                molecules[ind1].append(bond[1])
                atoms_used.append(bond[1])
            elif ind2 != -2:
                molecules[ind2].append(bond[0])
                atoms_used.append(bond[0])
            # Add new fragment if it does not connect to any other ones
            else:
                molecules.append(bond)
                atoms_used.extend(bond)

        # Add atoms that are not involved in bonds
        for atom in range(len(self.atoms)):
            if atom not in atoms_used:
                molecules.append([atom])

        # Sort molecules and store result
        self.mols_indices = tuple(sorted(molecule) for molecule in molecules)

###############################################################################

class Arrange3D(object):
    """
    Arranging of :class:`Molecule` or :class:`pybel.Molecule` molecule objects in
    3D space.
    The attributes are:

    =============== ================ ==========================================
    Attribute       Type             Description
    =============== ================ ==========================================
    `mols`          ``list``         A list of :class:`Molecule` objects
    =============== ================ ==========================================

    """

    def __init__(self, mols):
        if not 1 < len(mols) <= 4:
            raise Exception('More than 4 molecules are not supported')
        self.mols = mols

    def gen3D(self, forcefield='mmff94', make3D=True):
        """
        Generate 3D geometries for each molecule.
        """
        for mol in self.mols:
            smiles = mol.write().strip()
            if len(mol.atoms) == 1:  # Atoms
                mol.atoms[0].OBAtom.SetVector(0.0, 0.0, 0.0)
            elif smiles == '[H][H]':  # Hydrogen molecule
                mol.atoms[0].OBAtom.SetVector(0.0, 0.0, 0.0)
                mol.atoms[1].OBAtom.SetVector(0.74, 0.0, 0.0)
            elif smiles == 'O=O':  # Oxygen molecule
                mol.atoms[0].OBAtom.SetVector(0.0, 0.0, 0.0)
                mol.atoms[1].OBAtom.SetVector(1.21, 0.0, 0.0)
            else:
                make3DandOpt(mol, forcefield=forcefield, make3D=make3D)
                setLowestEnergyConformer(mol, forcefield=forcefield)

    def arrangeIn3D(self, forcefield='mmff94', d=3.5, make3D=True):
        """
        Arrange the molecules in 3D-space by modifying their coordinates. Two
        molecules are arranged in a line, three molecules in a triangle, and
        four molecules in a square. The molecules are separated by a distance
        `d` in Angstrom (excluding molecular radii).
        """
        self.gen3D(forcefield=forcefield, make3D=make3D)

        # Center molecules and find their approximate radii
        sizes = self.centerAndFindDistances()

        nmols = len(self.mols)
        # Separate molecules by `d` if there are two molecules
        if nmols == 2:
            t = pybel.ob.vector3(d + sizes[0] + sizes[1], 0.0, 0.0)
            self.mols[1].OBMol.Translate(t)
        # Arrange molecules in triangle if there are three molecules
        elif nmols == 3:
            t1 = pybel.ob.vector3(d + sizes[0] + sizes[1], 0.0, 0.0)
            d1 = d + sizes[0] + sizes[1]
            d2 = d + sizes[0] + sizes[2]
            d3 = d + sizes[1] + sizes[2]
            y = (-d1 ** 4.0 + 2.0 * d1 ** 2.0 * d2 ** 2.0 + 2.0 * d1 ** 2.0 * d3 ** 2.0 -
                 d2 ** 4.0 + 2.0 * d2 ** 2.0 * d3 ** 2.0 - d3 ** 4.0) ** 0.5 / (2.0 * d1)
            x = (d2 ** 2.0 - y ** 2.0) ** 0.5
            t2 = pybel.ob.vector3(x, -y, 0.0)
            self.mols[1].OBMol.Translate(t1)
            self.mols[2].OBMol.Translate(t2)
        # Arrange molecules in square if there are four molecules
        elif nmols == 4:
            x = max(d + sizes[0] + sizes[1], d + sizes[2] + sizes[3])
            y = max(d + sizes[0] + sizes[2], d + sizes[1] + sizes[3])
            t1 = pybel.ob.vector3(x, 0.0, 0.0)
            t2 = pybel.ob.vector3(0.0, -y, 0.0)
            t3 = pybel.ob.vector3(x, -y, 0.0)
            self.mols[1].OBMol.Translate(t1)
            self.mols[2].OBMol.Translate(t2)
            self.mols[3].OBMol.Translate(t3)

    def centerAndFindDistances(self):
        """
        Center the molecules about the origin and return the distances between
        the origin and the atom farthest from the origin, which can be used as
        size estimates for the molecules.
        """
        max_distances = []
        for mol in self.mols:
            mol.OBMol.ToInertialFrame()
            max_distance, distance_prev = 0.0, 0.0
            for atom in mol:
                distance = atom.vector.length()
                if distance > distance_prev:
                    max_distance = distance
                distance_prev = distance
            max_distances.append(max_distance)
        return max_distances